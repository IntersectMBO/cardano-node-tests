{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.14";
  src = fetchPypi {
    inherit pname version;
    sha256 = "15ii1x5lasfckvikc0phhv78jsajbdq3lyhpjcvkk9bs16iv7wkc";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
