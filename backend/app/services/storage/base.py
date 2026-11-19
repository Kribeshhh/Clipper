from typing import BinaryIO, Protocol
from uuid import UUID


class MediaStorage(Protocol):
    """Storage maps internal IDs to media. Callers close returned handles."""

    def put(self, key: UUID, source: BinaryIO) -> None: ...

    def open(self, key: UUID) -> BinaryIO: ...

    def delete(self, key: UUID) -> None: ...
