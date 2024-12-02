import pathlib as pl

FileType = str | pl.Path
FileTypeList = list[FileType] | list[str] | list[pl.Path]
# List of `FileType`s, empty list, or empty tuple
OptionalFiles = FileTypeList | tuple[()]
