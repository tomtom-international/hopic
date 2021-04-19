# Copyright (c) 2018 - 2020 TomTom N.V.
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

import re
from setuptools import setup

with open('README.rst', encoding='UTF-8') as fh:
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
    name='hopic',
    author='TomTom N.V.',
    description=description,
    long_description=long_description,
    long_description_content_type='text/x-rst',
    packages=(
        'hopic',
        'hopic.cli',
        'hopic.template',
    ),
    python_requires='>=3.6.5',
    install_requires=(
      'Click>=7.0,<8.0',
      'click-log',
      'commisery>=0.5,<1',
      'GitPython>=3,<4',
      'importlib_metadata; python_version < "3.8"',
      'python-dateutil',
      'PyYAML',
      'setuptools',
      'typeguard>=2.10,<3,!=2.11.0,!=2.11.1',
    ),
    setup_requires=(
      'setuptools_scm',
      'setuptools_scm_git_archive',
    ),
    extras_require={
        'interactive': ['keyring>=21.5.0,<22', 'netstruct>=1.1.2<2'],
    },
    use_scm_version={"relative_to": __file__, "local_scheme": "node-and-timestamp"},
    entry_points='''
      [console_scripts]
      hopic=hopic.cli.main:main
    ''',
    zip_safe=True,
    url='https://github.com/tomtom-international/hopic',
    project_urls={
      'Documentation': 'https://tomtom-international.github.io/hopic/',
      'Source Code': 'https://github.com/tomtom-international/hopic',
    },
    classifiers=(
      'License :: OSI Approved :: Apache Software License',
    ),
    license='Apache License 2.0',
    license_files='LICENSE',
)
