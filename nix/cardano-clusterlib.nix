{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc13";
  src = fetchPypi {
    inherit pname version;
    sha256 = "2BsVkJmzyJJ/pAAtoNAmhTJeIRQUaY6ocAtlD7jglt4=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
