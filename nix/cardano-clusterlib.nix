{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.42";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1vd88afjq42j2cxp75q1sn5lahxf2yhiq4n81cd8n1c6q1mawdk4";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
