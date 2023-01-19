{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.0rc3";
  src = fetchPypi {
    inherit pname version;
    sha256 = "SWmZvkcsxIftqZGQHK1ZAWSQK7cDlVMC63pzdhwZD84=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
