{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.0rc1";
  src = fetchPypi {
    inherit pname version;
    sha256 = "hXPPxjiMj3bG570DFLf+BXqKJbgWk/MO18VcTt1dQwg=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
