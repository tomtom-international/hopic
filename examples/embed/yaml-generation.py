#!/usr/bin/env python3
import sys

def main(argv):
  print("""
  test-%s:
    - echo generated test variant
  """ % argv[0]) 

if __name__ == "__main__":
   main(sys.argv[1:])
