#!/usr/bin/env bash

# install Allure
mkdir allure_install
curl -sfLo \
  allure_install/allure.tgz \
  "https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/2.13.9/allure-commandline-2.13.9.tgz"
tar xzf allure_install/allure.tgz -C allure_install
export PATH="$PWD/allure_install/allure-2.13.9/bin:$PATH"
