#!/user/env python3
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import os

try:
    with open('README.md') as f:
        readme = f.read()
except (IOError, UnicodeError):
    readme = ''

# requirements
try:
    with open(os.path.join(__file__, 'requirements.txt')) as fp:
        install_requires = fp.read().splitlines()
except (IOError, UnicodeError):
    install_requires = []


setup(
    name="bc4py-stratum-pool",
    version='0.0.2-alpha',
    url='https://github.com/namuyan/bc4py-stratum-pool',
    author='namuyan',
    description='python3 pool program for bc4py',
    long_description=readme,
    long_description_content_type='text/markdown',
    packages=find_packages(),
    install_requires=install_requires,
    license="MIT Licence",
    classifiers=[
        'Programming Language :: Python :: 3.6',
        'License :: OSI Approved :: MIT License',
    ],
)
