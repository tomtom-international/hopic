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

from pathlib import Path
import re
from setuptools import setup

long_description = (Path(__file__).parent / "README.rst").read_text(encoding="UTF-8")

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
      # The 3.1.15 version causes test_clean_checkout_in_non_empty_dir to fail due to a missing stderr.
      # See https://github.com/gitpython-developers/GitPython/issues/1221 for details.
      'GitPython>=3,<4,!=3.1.15',
      'importlib_metadata >= 3.6; python_version < "3.10.0b1"',
      'python-dateutil',
      'PyYAML',
      'setuptools',
      'typeguard>=2.10,<2.11,<3', # <2.11 should be removed after https://github.com/agronholm/typeguard/issues/175 is fixed
      'typing_extensions >= 3.6.4; python_version < "3.8"',
    ),
    extras_require={
        'interactive': ['keyring>=21.5.0,<22', 'netstruct>=1.1.2<2'],
    },
    entry_points='''
      [console_scripts]
      hopic=hopic.cli.main:main
    ''',
    zip_safe=True,
    url='https://github.com/tomtom-international/hopic',
    project_urls={
      'Documentation': 'https://tomtom-international.github.io/hopic/',
      "Change Log": "https://tomtom-international.github.io/hopic/changes.html",
      'Source Code': 'https://github.com/tomtom-international/hopic',
    },
    classifiers=(
      'License :: OSI Approved :: Apache Software License',
    ),
    license='Apache License 2.0',
    license_files='LICENSE',
)
