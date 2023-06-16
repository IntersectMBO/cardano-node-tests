import typing as tp
from pathlib import Path

FileType = tp.Union[str, Path]
FileTypeList = tp.Union[tp.List[str], tp.List[Path], tp.Set[str], tp.Set[Path]]
# list of `FileType`s, empty list, or empty tuple
OptionalFiles = tp.Union[FileTypeList, tp.Tuple[()]]
