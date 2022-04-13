{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.2.6";
  src = fetchPypi {
    inherit pname version;
    sha256 = "9PtZFd+Pig85jl76qr1tf0Nn49js1vwrVwV5s1xU5DM=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
