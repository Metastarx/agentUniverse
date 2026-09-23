# !/usr/bin/env python3
# -*- coding:utf-8 -*-

# @Time    : 2025/11/20 10:24
# @Author  : Saladday
# @Email   : fanjing.luo@zju.edu.cn
# @FileName: test_tar_reader.py
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from agentuniverse.agent.action.knowledge.reader.file import tar_reader as tar_reader_module
from agentuniverse.agent.action.knowledge.reader.file.file_reader import FileReader
from agentuniverse.agent.action.knowledge.reader.file.tar_reader import TarReader
from agentuniverse.agent.action.knowledge.reader.reader_manager import ReaderManager
from agentuniverse.base.component.component_enum import ComponentEnum
from agentuniverse.base.config.application_configer.app_configer import AppConfiger
from agentuniverse.base.config.application_configer.application_config_manager import ApplicationConfigManager
from agentuniverse.base.config.component_configer.component_configer import ComponentConfiger
from agentuniverse.base.config.configer import Configer

YAML_PATH = os.path.join(os.path.dirname(tar_reader_module.__file__), "tar_reader.yaml")


def _add_member(archive: tarfile.TarFile, name: str, payload) -> None:
    """Append a single regular file member carrying the given payload."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def _write_tar(path: Path, members, mode: str = "w") -> None:
    """Create a TAR archive out of (member name, payload) pairs."""
    with tarfile.open(path, mode) as archive:
        for name, payload in members:
            _add_member(archive, name, payload)


class TestTarReader(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.reader = TarReader()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reads_multiple_member_formats(self) -> None:
        archive_path = Path(self.temp_dir.name) / "bundle.tar"
        _write_tar(archive_path, [
            ("README.md", "# 主文档\n\n这是根目录的说明文件"),
            ("docs/intro.txt", "欢迎使用TAR压缩包测试系统"),
            ("src/main.py", "def main():\n    print('Hello from TAR')\n"),
            ("config/settings.json", '{"app": "test", "version": "1.0.0"}'),
            ("data/sample.csv", "Name,Age\nAlice,28\nBob,32"),
        ])

        docs = self.reader._load_data(archive_path)

        self.assertEqual(len(docs), 5)
        by_name = {doc.metadata["file_name"]: doc for doc in docs}
        self.assertEqual(
            set(by_name),
            {"README.md", "intro.txt", "main.py", "settings.json", "sample.csv"},
        )
        for doc in docs:
            self.assertEqual(doc.metadata["archive_root"], "bundle.tar")
            self.assertEqual(doc.metadata["archive_depth"], 0)
            self.assertTrue(doc.metadata["file_path"].endswith("::" + doc.metadata["archive_path"]))

        self.assertIn("欢迎使用", by_name["intro.txt"].text)
        self.assertEqual(by_name["intro.txt"].metadata["archive_path"], "docs/intro.txt")
        self.assertIn("Hello from TAR", by_name["main.py"].text)
        self.assertIn("主文档", by_name["README.md"].text)
        self.assertEqual(by_name["settings.json"].text, '{"app": "test", "version": "1.0.0"}')
        self.assertIn("Alice, 28", by_name["sample.csv"].text)

    def test_nested_tar_member_is_expanded_in_place(self) -> None:
        inner = io.BytesIO()
        with tarfile.open(fileobj=inner, mode="w") as archive:
            _add_member(archive, "deep/inner.txt", "内部嵌套内容")
        inner_bytes = inner.getvalue()

        archive_path = Path(self.temp_dir.name) / "outer.tar"
        with tarfile.open(archive_path, "w") as archive:
            _add_member(archive, "container/inner.tar", inner_bytes)
            _add_member(archive, "outer.txt", "外部内容")

        docs = self.reader._load_data(archive_path)

        self.assertEqual(len(docs), 2)
        nested = [doc for doc in docs if doc.metadata["file_name"] == "inner.txt"]
        self.assertEqual(len(nested), 1)
        self.assertEqual(nested[0].metadata["archive_root"], "outer.tar")
        self.assertEqual(nested[0].metadata["archive_depth"], 1)
        self.assertEqual(nested[0].metadata["archive_path"], "container/inner.tar/deep/inner.txt")
        self.assertIn("内部嵌套内容", nested[0].text)

        top_level = [doc for doc in docs if doc.metadata["file_name"] == "outer.txt"]
        self.assertEqual(len(top_level), 1)
        self.assertEqual(top_level[0].metadata["archive_depth"], 0)

    def test_gzip_and_tgz_variants_behave_like_tar(self) -> None:
        members = [("a.txt", "compressed content"), ("b.txt", "更多内容")]
        for archive_name in ("bundle.tar.gz", "bundle.tgz"):
            archive_path = Path(self.temp_dir.name) / archive_name
            _write_tar(archive_path, members, mode="w:gz")

            docs = self.reader._load_data(archive_path)

            self.assertEqual(len(docs), 2)
            self.assertEqual({doc.metadata["archive_root"] for doc in docs}, {archive_name})
            self.assertEqual({doc.metadata["archive_depth"] for doc in docs}, {0})
            by_name = {doc.metadata["file_name"]: doc for doc in docs}
            self.assertIn("compressed content", by_name["a.txt"].text)
            self.assertIn("更多内容", by_name["b.txt"].text)

    def test_unsafe_member_names_are_skipped(self) -> None:
        archive_path = Path(self.temp_dir.name) / "unsafe.tar"
        _write_tar(archive_path, [
            ("../escape.txt", "must never be read"),
            ("/abs.txt", "must never be read"),
            ("safe.txt", "safe content"),
        ])

        docs = self.reader._load_data(archive_path)

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata["file_name"], "safe.txt")
        # members must never be materialised outside of the temporary dir
        self.assertFalse((Path(self.temp_dir.name) / "escape.txt").exists())
        self.assertFalse((Path(self.temp_dir.name).parent / "escape.txt").exists())

    def test_max_files_limit(self) -> None:
        archive_path = Path(self.temp_dir.name) / "two_members.tar"
        _write_tar(archive_path, [("a.txt", "a"), ("b.txt", "b")])

        with self.assertRaises(ValueError):
            TarReader(max_files=1)._load_data(archive_path)

    def test_max_file_size_limit(self) -> None:
        archive_path = Path(self.temp_dir.name) / "large_member.tar"
        _write_tar(archive_path, [("large.txt", "a" * 4096)])

        with self.assertRaises(ValueError):
            TarReader(max_file_size=1024, max_total_size=2048)._load_data(archive_path)

    def test_max_total_size_limit(self) -> None:
        archive_path = Path(self.temp_dir.name) / "total_size.tar"
        _write_tar(archive_path, [("a.txt", "a" * 800), ("b.txt", "b" * 800)])

        with self.assertRaises(ValueError):
            TarReader(max_file_size=1024, max_total_size=1000)._load_data(archive_path)

    def test_compression_ratio_limit(self) -> None:
        archive_path = Path(self.temp_dir.name) / "compressible.tar.gz"
        _write_tar(archive_path, [("repetitive.txt", "a" * 200000)], mode="w:gz")

        with self.assertRaises(ValueError):
            TarReader(max_compression_ratio=10)._load_data(archive_path)

    def test_compression_ratio_guard_ignores_plain_tar(self) -> None:
        # A plain ".tar" is stored uncompressed, so the default ratio guard
        # must never reject its members.
        archive_path = Path(self.temp_dir.name) / "plain.tar"
        _write_tar(archive_path, [("a.txt", "a" * 4096)])

        docs = TarReader()._load_data(archive_path)

        self.assertEqual(len(docs), 1)

    def test_max_depth_limit(self) -> None:
        level3 = io.BytesIO()
        with tarfile.open(fileobj=level3, mode="w") as archive:
            _add_member(archive, "deepest.txt", "deepest content")
        level2 = io.BytesIO()
        with tarfile.open(fileobj=level2, mode="w") as archive:
            _add_member(archive, "level3.tar", level3.getvalue())
        archive_path = Path(self.temp_dir.name) / "level1.tar"
        _write_tar(archive_path, [("level2.tar", level2.getvalue())])

        # the very same archive is readable when enough depth is allowed
        self.assertEqual(len(TarReader(max_depth=5)._load_data(archive_path)), 1)

        with self.assertRaises(ValueError):
            TarReader(max_depth=1)._load_data(archive_path)

    def test_link_members_are_ignored(self) -> None:
        archive_path = Path(self.temp_dir.name) / "links.tar"
        with tarfile.open(archive_path, "w") as archive:
            symlink = tarfile.TarInfo("link.txt")
            symlink.type = tarfile.SYMTYPE
            symlink.linkname = "target.txt"
            archive.addfile(symlink)

            hardlink = tarfile.TarInfo("hard.txt")
            hardlink.type = tarfile.LNKTYPE
            hardlink.linkname = "target.txt"
            archive.addfile(hardlink)

            _add_member(archive, "target.txt", "real content")

        docs = self.reader._load_data(archive_path)

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata["file_name"], "target.txt")

    def test_empty_archive_returns_no_documents(self) -> None:
        archive_path = Path(self.temp_dir.name) / "empty.tar"
        with tarfile.open(archive_path, "w"):
            pass

        self.assertEqual(self.reader._load_data(archive_path), [])

    def test_repeated_load_is_idempotent(self) -> None:
        # The per-archive counters must be reset on every call, otherwise the
        # second load of the same archive would trip the size limits.
        archive_path = Path(self.temp_dir.name) / "repeat.tar"
        _write_tar(archive_path, [("a.txt", "content")])

        first = self.reader._load_data(archive_path)
        second = self.reader._load_data(archive_path)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0].text, second[0].text)

    def test_ext_info_is_merged_into_metadata(self) -> None:
        archive_path = Path(self.temp_dir.name) / "meta.tar"
        _write_tar(archive_path, [("file.txt", "content")])

        docs = self.reader._load_data(archive_path, ext_info={"project": "demo"})

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata["project"], "demo")
        self.assertEqual(docs[0].metadata["archive_root"], "meta.tar")

    def test_non_archive_file_raises(self) -> None:
        path = Path(self.temp_dir.name) / "not_an_archive.tar"
        path.write_text("this is not a tar archive", encoding="utf-8")

        # a clear failure instead of a silently empty document list
        with self.assertRaises(tarfile.TarError):
            self.reader._load_data(path)

    def test_missing_file_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.reader._load_data(Path(self.temp_dir.name) / "missing.tar")


class TestTarReaderRoutingAndRegistration(unittest.TestCase):
    """The shipped component is wired into FileReader, ReaderManager and Knowledge."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        try:
            self.previous_app_configer = ApplicationConfigManager().app_configer
        except ValueError:
            self.previous_app_configer = None
        ApplicationConfigManager().app_configer = AppConfiger()
        self.component_configer = ComponentConfiger().load_by_configer(
            Configer(path=os.path.abspath(YAML_PATH)).load()
        )

    def tearDown(self) -> None:
        ApplicationConfigManager().app_configer = self.previous_app_configer
        self.temp_dir.cleanup()

    def _register_default_tar_reader(self) -> ReaderManager:
        reader = TarReader().initialize_by_component_configer(self.component_configer)
        manager = ReaderManager()
        manager.register(reader.get_instance_code(), reader)
        self.addCleanup(manager.unregister, reader.get_instance_code())
        return manager

    def test_file_reader_routes_compound_tar_gz_suffix(self) -> None:
        archive_path = Path(self.temp_dir.name) / "bundle.tar.gz"
        _write_tar(archive_path, [("hello.txt", "hello from tar.gz")], mode="w:gz")

        docs = FileReader()._load_data([archive_path])

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata["file_name"], "hello.txt")
        self.assertIn("hello from tar.gz", docs[0].text)

    def test_file_reader_routes_plain_tar_suffix(self) -> None:
        archive_path = Path(self.temp_dir.name) / "bundle.tar"
        _write_tar(archive_path, [("hello.txt", "hello from tar")])

        docs = FileReader()._load_data([archive_path])

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata["file_name"], "hello.txt")

    def test_default_reader_aliases(self) -> None:
        for file_type in ("tar", "tgz", "tar.gz"):
            self.assertEqual(ReaderManager.DEFAULT_READER.get(file_type), "default_tar_reader")

    def test_yaml_declares_tar_reader_component(self) -> None:
        self.assertEqual(
            self.component_configer.get_component_config_type(),
            ComponentEnum.READER.value,
        )
        self.assertEqual(
            self.component_configer.metadata_module,
            "agentuniverse.agent.action.knowledge.reader.file.tar_reader",
        )
        self.assertEqual(self.component_configer.metadata_class, "TarReader")

    def test_reader_manager_resolves_tar_default_reader(self) -> None:
        manager = self._register_default_tar_reader()

        for file_type in ("tar", "tgz", "tar.gz"):
            self.assertIsInstance(manager.get_file_default_reader(file_type), TarReader)

    def test_knowledge_resolves_compound_source_type(self) -> None:
        from agentuniverse.agent.action.knowledge.knowledge import Knowledge

        self._register_default_tar_reader()
        archive_path = Path(self.temp_dir.name) / "bundle.tar.gz"
        _write_tar(archive_path, [("hello.txt", "hello from knowledge")], mode="w:gz")

        docs = Knowledge(name="tar_knowledge")._load_data(source_path=str(archive_path))

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata["file_name"], "hello.txt")
        self.assertIn("hello from knowledge", docs[0].text)


if __name__ == "__main__":
    unittest.main()
