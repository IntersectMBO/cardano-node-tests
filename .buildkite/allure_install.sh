#!/usr/bin/env bash

ALLURE_VERSION="2.17.3"

# install Allure
mkdir allure_install
curl -sfLo \
  allure_install/allure.tgz \
  "https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/${ALLURE_VERSION}/allure-commandline-${ALLURE_VERSION}.tgz"
tar xzf allure_install/allure.tgz -C allure_install
export PATH="$PWD/allure_install/allure-${ALLURE_VERSION}/bin:$PATH"
