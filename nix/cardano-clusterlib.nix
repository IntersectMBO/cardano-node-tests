{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.0";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0+q879Eld5bb4lzH0cItPyr93h8jsYdFJRL7a+PDAuk=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
