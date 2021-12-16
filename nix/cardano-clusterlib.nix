{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.49";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0w9BbhOrlyDwGH/B6CB9UAsld9bVE4oNtdsGaPWVVdw=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
