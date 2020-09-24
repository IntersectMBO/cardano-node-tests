from pathlib import Path
from typing import List
from typing import Tuple
from typing import Union

FileType = Union[str, Path]
FileTypeList = Union[List[str], List[Path]]
UnpackableSequence = Union[list, tuple]
# list of `FileType`s, empty list, or empty tuple
OptionalFiles = Union[FileTypeList, Tuple[()]]
