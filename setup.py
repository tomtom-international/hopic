from setuptools import setup

setup(
    name='cidriver',
    packages=('cidriver',),
    py_modules=('cidriver',),
    install_requires=(
      'Click',
      'python-dateutil',
      'PyYAML',
    ),
    setup_requires=(
      'setuptools_scm',
      'setuptools_scm_git_archive',
    ),
    use_scm_version={"root": "../..", "relative_to": __file__},
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
