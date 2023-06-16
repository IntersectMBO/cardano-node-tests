import pathlib as pl
import typing as tp

FileType = tp.Union[str, pl.Path]
FileTypeList = tp.Union[tp.List[str], tp.List[pl.Path], tp.Set[str], tp.Set[pl.Path]]
# list of `FileType`s, empty list, or empty tuple
OptionalFiles = tp.Union[FileTypeList, tp.Tuple[()]]
