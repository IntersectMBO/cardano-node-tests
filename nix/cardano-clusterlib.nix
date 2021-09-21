{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.44";
  src = fetchPypi {
    inherit pname version;
    sha256 = "06m9iffhxn37qjyp95ar9rw6hvz4gr78p3bnm5jyramjdshwrbcw";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
