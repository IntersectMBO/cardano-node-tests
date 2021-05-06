{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.16";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0dzr3621syj82b1v9r1ggq69hrkfv931xrmijpslgkb42ba7j94d";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
