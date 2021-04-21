{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.12";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1jx3jjbnb12ih1cg2b4bsap1r4zkwfp3czs3krfr6dsiq2r2km53";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
