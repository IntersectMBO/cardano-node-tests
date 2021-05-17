{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.20";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0lvakb6if3rmyvyam36q5mfh7q05kcggfszk9ksivdkvb74cq812";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
