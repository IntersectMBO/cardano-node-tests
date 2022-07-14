{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc8";
  src = fetchPypi {
    inherit pname version;
    sha256 = "9erauFqdzCvlg+QZ+vDpLB5RjjGMq0bGutyic+sIcT0=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
