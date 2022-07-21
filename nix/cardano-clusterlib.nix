{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc11";
  src = fetchPypi {
    inherit pname version;
    sha256 = "KHinOtlo02XHHVsJabWU7zjvsqjf6PMGXmbaEg9dzkw=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
