{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.13";
  src = fetchPypi {
    inherit pname version;
    sha256 = "07h96vpmyh3bc6s5dlqgsz4gpsfzkd91xn7wvs154176237hhgx7";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
