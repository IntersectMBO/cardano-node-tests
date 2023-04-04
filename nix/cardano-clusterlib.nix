{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.3";
  src = fetchPypi {
    inherit pname version;
    sha256 = "86c7dI6iC+cma3ZwDw1iEEXN06/Yva7yX/5peLxaq04=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
