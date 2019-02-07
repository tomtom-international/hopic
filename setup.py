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

import io
import re
from setuptools import setup

with io.open('README.rst', encoding='UTF-8') as fh:
    long_description = fh.read()

# Remove directives
description = re.sub(r'^[ \t]*\.\.(?:[ \t]+[^\n]*)?\n(?:[ \t]+[^\n]*\n)*', r'', long_description, flags=re.DOTALL|re.MULTILINE)
description = description.strip()
# Extract first paragraph
description = re.sub(r'\n\n.*', r'', description, flags=re.DOTALL|re.MULTILINE)
# Eliminate emphasis annotation
description = re.sub(r'\*\*(.*?)\*\*', r'\1', description)
# Convert line breaks into spaces
description = description.replace('\n', ' ')

setup(
    name='cidriver',
    author='TomTom N.V.',
    description=description,
    long_description=long_description,
    long_description_content_type='text/x-rst',
    packages=('cidriver',),
    py_modules=('cidriver',),
    install_requires=(
      'Click>=7.0,<8.0',
      'click-log',
      'GitPython>=2.1.3,<3',
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
