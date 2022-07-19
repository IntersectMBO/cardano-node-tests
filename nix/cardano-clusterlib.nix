{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc10";
  src = fetchPypi {
    inherit pname version;
    sha256 = "GFSxRL6uZ91WXHtdu3T78+t+cXPbnwi5Q38CbMpQTkw=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
