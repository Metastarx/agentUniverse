# !/usr/bin/env python3
# -*- coding:utf-8 -*-

# @Time    : 2025/11/20 10:24
# @Author  : Saladday
# @Email   : fanjing.luo@zju.edu.cn
# @FileName: tar_reader.py
import io
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Union

from agentuniverse.agent.action.knowledge.reader.file.code_reader import CODE_FILE_EXTENSIONS, CodeReader
from agentuniverse.agent.action.knowledge.reader.file.csv_reader import CSVReader
from agentuniverse.agent.action.knowledge.reader.file.docx_reader import DocxReader
from agentuniverse.agent.action.knowledge.reader.file.epub_reader import EpubReader
from agentuniverse.agent.action.knowledge.reader.file.markdown_reader import MarkdownReader
from agentuniverse.agent.action.knowledge.reader.file.pdf_reader import PdfReader
from agentuniverse.agent.action.knowledge.reader.file.pptx_reader import PptxReader
from agentuniverse.agent.action.knowledge.reader.file.txt_reader import TxtReader
from agentuniverse.agent.action.knowledge.reader.file.xlsx_reader import XlsxReader
from agentuniverse.agent.action.knowledge.reader.file.zip_reader import TEXT_FALLBACK_EXTENSIONS
from agentuniverse.agent.action.knowledge.reader.reader import Reader
from agentuniverse.agent.action.knowledge.store.document import Document

# Suffixes that identify an archive member as a nested TAR archive. TAR
# payloads are normally compressed as a whole (".tar.gz", ".tgz", ...), so the
# plain last suffix of a member is not enough to recognise one.
TAR_MEMBER_SUFFIXES = {
    ".tar",
    ".tgz",
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
}


class TarReader(Reader):
    """TAR archive reader for the knowledge base.

    TAR is the archive format of the reader family whose support ships with the
    Python standard library, so unlike the RAR/7z readers this component works
    in every environment. The behaviour mirrors ZipReader: members are streamed
    through the same sub-readers, the same safety limits apply and the produced
    Document metadata follows the identical contract.
    """

    max_total_size: int = 512 * 1024 * 1024
    max_file_size: int = 64 * 1024 * 1024
    max_depth: int = 5
    max_files: int = 4096
    max_compression_ratio: int = 100
    stream_chunk_size: int = 1024 * 1024

    def _get_reader(self, suffix: str) -> Optional[Reader]:
        if suffix not in self._readers:
            if suffix in CODE_FILE_EXTENSIONS:
                self._readers[suffix] = CodeReader()
            elif suffix in self._reader_classes:
                self._readers[suffix] = self._reader_classes[suffix]()
        return self._readers.get(suffix)

    def _load_data(self, file: Union[str, Path], ext_info: Optional[Dict] = None) -> List[Document]:
        if isinstance(file, str):
            file = Path(file)
        if not isinstance(file, Path):
            raise TypeError("file must be path-like")
        if not file.exists():
            raise FileNotFoundError(f"Tar file not found: {file}")

        self._total_size = 0
        self._file_count = 0
        self._readers = {}
        self._reader_classes = {
            ".csv": CSVReader,
            ".txt": TxtReader,
            ".md": MarkdownReader,
            ".pdf": PdfReader,
            ".docx": DocxReader,
            ".pptx": PptxReader,
            ".xlsx": XlsxReader,
            ".epub": EpubReader,
        }
        # TAR keeps no per-member compressed size, so the decompression-bomb
        # guard is evaluated against the archive as a whole. For an
        # uncompressed ".tar" this is simply the size of the payload on disk.
        self._compressed_size = file.stat().st_size

        ext_meta = dict(ext_info or {})
        # "r:*" transparently detects plain, gzip, bzip2 and xz compressed TAR
        # archives, so ".tar", ".tar.gz", ".tgz", ... share a single code path.
        with tarfile.open(file, mode="r:*") as archive:
            with tempfile.TemporaryDirectory() as temp_dir:
                return self._iterate_archive(
                    archive,
                    file,
                    Path(temp_dir),
                    ext_meta,
                    0,
                    [],
                )

    def _iterate_archive(
        self,
        archive: tarfile.TarFile,
        archive_path: Path,
        temp_dir: Path,
        ext_meta: Dict,
        depth: int,
        path_stack: List[str],
    ) -> List[Document]:
        documents: List[Document] = []
        for member in archive.getmembers():
            if member.isdir():
                continue
            member_path = self._normalize_member(member.name)
            if member_path is None:
                continue
            # Links and special members are dropped instead of being followed:
            # like the symlink entries ZipReader refuses, a link can point at a
            # path outside of the archive.
            if not member.isfile():
                continue
            self._enforce_limits(member)
            suffix = member_path.suffix.lower()
            current_stack = path_stack + [member_path.as_posix()]
            metadata = self._build_metadata(archive_path, current_stack, depth, ext_meta)

            if self._is_nested_tar(member_path):
                documents.extend(
                    self._handle_nested_tar(
                        archive,
                        member,
                        archive_path,
                        temp_dir,
                        ext_meta,
                        depth,
                        current_stack,
                    )
                )
            elif suffix in TEXT_FALLBACK_EXTENSIONS:
                documents.extend(
                    self._handle_text_fallback(archive, member, metadata)
                )
            elif suffix in CODE_FILE_EXTENSIONS or suffix in self._reader_classes:
                reader = self._get_reader(suffix)
                if reader:
                    documents.extend(
                        self._handle_reader_with_temp(archive, member, temp_dir, metadata, reader)
                    )
        return documents

    @staticmethod
    def _is_nested_tar(member_path: PurePosixPath) -> bool:
        """Return whether an archive member is a TAR archive itself.

        TAR members are frequently compressed (e.g. "inner.tar.gz"), so the
        plain last suffix alone does not identify them.
        """
        return "".join(member_path.suffixes[-2:]).lower() in TAR_MEMBER_SUFFIXES

    def _handle_nested_tar(
        self,
        archive: tarfile.TarFile,
        member: tarfile.TarInfo,
        archive_path: Path,
        temp_dir: Path,
        ext_meta: Dict,
        depth: int,
        current_stack: List[str],
    ) -> List[Document]:
        if depth + 1 > self.max_depth:
            raise ValueError("Tar nesting depth exceeded")

        data = None
        try:
            source = archive.extractfile(member)
            if source is None:
                return []
            # The nested archive is expanded in place, on the caller's stack,
            # so the depth counter and the caller's limits keep applying to
            # the entries of the nested archive.
            with source:
                data = source.read()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as nested:
                return self._iterate_archive(
                    nested,
                    archive_path,
                    temp_dir,
                    ext_meta,
                    depth + 1,
                    current_stack,
                )
        except tarfile.TarError as exc:
            raise ValueError("Invalid nested tar content") from exc
        finally:
            del data

    def _handle_reader_with_temp(
        self,
        archive: tarfile.TarFile,
        member: tarfile.TarInfo,
        temp_dir: Path,
        metadata: Dict,
        reader: Reader,
    ) -> List[Document]:
        file_path = self._write_temp_file(archive, member, temp_dir)
        try:
            docs = reader.load_data(file_path, ext_info=dict(metadata))
            return [self._merge_metadata(doc, metadata) for doc in docs]
        except Exception:
            return []
        finally:
            if file_path.exists():
                file_path.unlink()

    def _handle_text_fallback(
        self,
        archive: tarfile.TarFile,
        member: tarfile.TarInfo,
        metadata: Dict,
    ) -> List[Document]:
        source = archive.extractfile(member)
        if source is None:
            return []
        with source:
            text = self._read_text(source)
        if not text:
            return []
        return [Document(text=text, metadata=dict(metadata))]

    def _write_temp_file(
        self,
        archive: tarfile.TarFile,
        member: tarfile.TarInfo,
        temp_dir: Path,
    ) -> Path:
        name = PurePosixPath(member.name).name
        if not name:
            name = uuid.uuid4().hex
        file_path = temp_dir / f"{uuid.uuid4().hex}_{name}"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"Tar entry cannot be read: {member.name}")
        with source, open(file_path, "wb") as target:
            shutil.copyfileobj(source, target, self.stream_chunk_size)
        return file_path

    def _merge_metadata(self, document: Document, metadata: Dict) -> Document:
        if document.metadata is None:
            document.metadata = {}
        for key in ["file_name", "file_path"]:
            if key in metadata:
                document.metadata[key] = metadata[key]
        document.metadata.update({k: v for k, v in metadata.items() if k not in document.metadata})
        return document

    def _normalize_member(self, member: str) -> Optional[PurePosixPath]:
        if not member:
            return None
        normalized = PurePosixPath(member)
        if normalized.is_absolute() or ".." in normalized.parts:
            return None
        parts = [part for part in normalized.parts if part not in {"", ".", ".."}]
        if not parts:
            return None
        return PurePosixPath(*parts)

    def _build_metadata(
        self,
        archive_path: Path,
        path_stack: List[str],
        depth: int,
        ext_meta: Dict,
    ) -> Dict:
        metadata = {
            "archive_root": archive_path.name,
            "archive_path": "/".join(path_stack),
            "archive_depth": depth,
            "file_name": PurePosixPath(path_stack[-1]).name if path_stack else archive_path.name,
            "file_path": f"{archive_path.as_posix()}::{ '/'.join(path_stack) if path_stack else '' }".rstrip(":"),
        }
        if ext_meta:
            metadata.update(ext_meta)
        return metadata

    def _read_text(self, stream: io.BufferedReader) -> str:
        text_chunks: List[str] = []
        reader = io.TextIOWrapper(stream, encoding="utf-8", errors="ignore")
        while True:
            chunk = reader.read(self.stream_chunk_size)
            if not chunk:
                break
            text_chunks.append(chunk)
        return "".join(text_chunks)

    def _enforce_limits(self, member: tarfile.TarInfo) -> None:
        size = member.size

        if size > self.max_file_size:
            raise ValueError(f"Tar entry exceeds maximum size: {member.name}")
        if self._total_size + size > self.max_total_size:
            raise ValueError("Tar archive exceeds maximum total size")
        if self._file_count + 1 > self.max_files:
            raise ValueError("Tar archive exceeds maximum file count")

        if self._compressed_size > 0:
            compression_ratio = (self._total_size + size) / self._compressed_size
            if compression_ratio > self.max_compression_ratio:
                raise ValueError(f"Tar archive has suspicious compression ratio: {member.name}")

        self._total_size += size
        self._file_count += 1
