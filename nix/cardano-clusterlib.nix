{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.41";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1mcxj2yr1lywg7ggp5hlfkg0mik3l69lls5fqhkbnv5vvc8v66gh";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
