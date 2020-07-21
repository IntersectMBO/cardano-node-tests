from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

FileType = Union[str, Path]
UnpackableSequence = Union[list, tuple]
# list of `FileType`s, empty list, or empty tuple
OptionalFiles = Union[List[Optional[FileType]], Tuple[()]]
