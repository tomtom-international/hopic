# Copyright (c) 2018 - 2019 TomTom N.V. (https://tomtom.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from setuptools import setup

setup(
    name='cidriver',
    packages=('cidriver',),
    py_modules=('cidriver',),
    install_requires=(
      'Click>=7.0,<8.0',
      'python-dateutil',
      'PyYAML',
      'six',
    ),
    setup_requires=(
      'setuptools_scm',
      'setuptools_scm_git_archive',
    ),
    use_scm_version={"relative_to": __file__},
    entry_points='''
      [console_scripts]
      ci-driver=cidriver.cli:cli
    ''',
    url='https://github.com/tomtom-international/hopic',
    project_urls={
      'Source Code': 'https://github.com/tomtom-international/hopic',
    },
    classifiers=(
      'License :: OSI Approved :: Apache Software License',
    ),
    license='Apache License 2.0',
    license_files='LICENSE',
)
