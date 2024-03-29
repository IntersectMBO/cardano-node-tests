import pathlib as pl
import typing as tp

FileType = tp.Union[str, pl.Path]
FileTypeList = tp.Union[tp.List[FileType], tp.List[str], tp.List[pl.Path]]
# list of `FileType`s, empty list, or empty tuple
OptionalFiles = tp.Union[FileTypeList, tp.Tuple[()]]
