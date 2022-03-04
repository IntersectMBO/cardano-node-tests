{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.2.4";
  src = fetchPypi {
    inherit pname version;
    sha256 = "JbpP8Kar8s11F/kZ7UsoxOkhMviqtkuSuBBHd9cDgks=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
