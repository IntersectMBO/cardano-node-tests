{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.17";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1sh6q34yw5ayy7wsjsyhq2iv4pk1c532a7pfzvm2h9aqwbswq0bg";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
