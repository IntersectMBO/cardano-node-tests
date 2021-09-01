{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.39";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0ri4mds1ixplyyrlx2pxybz5lc53s6x57hvalm6iy0jz6qjf1m66";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
