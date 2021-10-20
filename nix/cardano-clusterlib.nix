{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.46";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1mxsy6bmc4lc6v466m8w64giwfrij1j6qvx78hif6r23ddq9inxh";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
