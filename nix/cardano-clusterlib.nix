{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.2.2";
  src = fetchPypi {
    inherit pname version;
    sha256 = "sgf67AusQaB3S/WoFlLCIcfmU/kHHgcdZ5NuIg0OaBE=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
