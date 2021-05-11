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

  private def node_execution_to_map(obj) {
    return [
      'allocation_group' : obj.allocation_group,
      'exec_name' : obj.exec_name,
      'start_time' : obj.start_time,
      'end_time' : obj.end_time,
      'request_time' : obj.request_time,
      'status' : obj.status,
    ]
  }

  private def class_properties_to_list_of_maps(String propertyName, Map map) {
    return map.collect { key, value ->
      def transformed_array = value.collect { elem ->
        def newMap = elem instanceof NodeExecution ? node_execution_to_map(elem) : elem
        newMap[propertyName] = key
        return newMap
      }
      return transformed_array
    }.flatten() 
  }

  @NonCPS
  private def sort_each_value_of_map_on_end_time(map) {
    return map.each { k, v ->
      v.sort { a,b ->
        b.end_time <=> a.end_time
      }
    }
  }

  @NonCPS
  private def sort_map_on_end_time(map) {
    map.sort { 
      it.value.collect{it.end_time}.max { it }
    }
  }

  private def add_critical_path_entry(allocation) {
    return ['execution': [
      'executor': allocation.executor,
      'node': allocation.node,
      'start_time': allocation.start_time,
    ],
      'critical_path': String.format("  ${allocation.exec_name} (%s - %s)\n",
      new SimpleDateFormat("HH:mm:ss").format(epoch_to_UTC_time(allocation.start_time)),
      new SimpleDateFormat("HH:mm:ss").format(epoch_to_UTC_time(allocation.end_time))
    )]
  }

  public def print_critical_path(nodes_usage) {
    // transform nodes_usage map from [[<node_name>]:[<executor>:[data]]] -> [<allocation_group>:[data]]
    def allocation_groups = nodes_usage.collectEntries { k, v -> [k, class_properties_to_list_of_maps('executor', v) ]} // 1: put 'executor' key in map
    allocation_groups = allocation_groups.collectEntries { [it.key, class_properties_to_list_of_maps('node', allocation_groups)] } // 2: put 'node' key in map
      .collectEntries { node_execution_map -> // 3: transform map to have allocation_group as key with array of previously transformed items
        node_execution_map.value.collect { node_execution -> node_execution.allocation_group } // 3.1 get all allocation groups
        .unique() // 3.2 get unique allocation groups
        .collectEntries { allocation -> [(allocation): node_execution_map.value.findAll { execution ->
          allocation == execution.allocation_group
        }]} // 3.3 make allocation group as key
      }
    
    allocation_groups = this.sort_each_value_of_map_on_end_time(allocation_groups)
    allocation_groups = this.sort_map_on_end_time(allocation_groups)

    def criticalPath = "Critical path:\n"
    allocation_groups.each {
      def execution = [:]
      def criticalPathEntries = []

      // critical path from last finishing node 
      it.value.each { allocation ->
        if (!execution || (allocation.end_time <= execution.start_time && allocation.node == execution.node && allocation.executor == execution.executor)) {
          def critical_path_entry = add_critical_path_entry(allocation)
          execution = critical_path_entry.execution
          criticalPathEntries.push(critical_path_entry.critical_path)
        }
      }

      // executions on different nodes that were scheduled before critical node
      it.value.each { allocation ->
        if (!execution || allocation.end_time <= execution.start_time) {
          def critical_path_entry = add_critical_path_entry(allocation)
          execution = critical_path_entry.execution
          criticalPathEntries.push(critical_path_entry.critical_path)
        }
      }

      criticalPath += criticalPathEntries.reverse().join('')
    }
    steps.println(criticalPath.trim())
  }
}

