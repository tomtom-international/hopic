/*
 * Copyright (c) 2021 TomTom N.V.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */ 

import java.text.SimpleDateFormat

def call(steps) {
  return new PrintMetrics(steps)
}

class PrintMetrics {
  protected steps

  PrintMetrics(steps) {
    this.steps = steps
  }

  private epoch_to_UTC_time(long time) {
    return new Date(time)
  }

  public def print_node_usage(nodes_usage) {
    def largest_name_size = nodes_usage.collect { it.value.collect { it.value.collect { it.exec_name.size() }}}.flatten().max { it }
    String printable_string = ""
    nodes_usage.each { node, executor ->
      printable_string += "node: ${node}\n"
        def nesting_spaces = 2
        executor.each { executor_number, allocation ->
          if (executor.size() > 1) {
            printable_string += "  executor number: ${executor_number}\n"
            nesting_spaces = 4
          }
          allocation.each {
            printable_string += String.format("${' '.multiply(nesting_spaces)}%-${largest_name_size}s request time: %s start time: %s end time: %s status: %s\n",
              it.exec_name,
              new SimpleDateFormat("HH:mm:ss").format(epoch_to_UTC_time(it.request_time)),
              new SimpleDateFormat("HH:mm:ss").format(epoch_to_UTC_time(it.start_time)),
              new SimpleDateFormat("HH:mm:ss").format(epoch_to_UTC_time(it.end_time)),
              it.status
            )
          }
        }
      printable_string += "\n"
    }
    steps.print(printable_string.trim())
  }
}
