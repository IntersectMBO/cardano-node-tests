{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc18";
  src = fetchPypi {
    inherit pname version;
    sha256 = "JU2fpw9oyH6CPHDd+XglMKEYlY51wQCZuMcjqCC4oyI=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
