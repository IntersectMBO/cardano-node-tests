{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.36";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0yfyi03pad60qjnd2rq4mcqw8y6dl8g3crxgcxgdak9n8a6z5gsn";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
