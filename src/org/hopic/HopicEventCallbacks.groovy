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

package org.hopic

public class HopicEventCallbacks {

  /**
  * Gets called at the end of the CiDriver build method.
  * This function is NOT called on a node!
  *
  * @param exception  exception from the build or null when there is no exception
  */
  def on_build_end(exception) {}

  /**
  * Gets called at the start of a variant
  * This function is called on a node!
  *
  * @param phase      The phase where the variant is part of
  * @param variant    The variant name
  */
  def on_variant_start(String phase, String variant) {}

  /**
  * Gets called at the end of a variant
  * This function is called on a node!
  *
  * @param phase      The phase where the variant is part of
  * @param variant    The variant name
  * @param exception  Exception from the build or null when there is no exception
  */
  def on_variant_end(String phase, String variant, exception) {}
  def on_node_requested(String phase, String variant, String stage_name, String node_expr, Integer allocation_id) {}
  def on_node_acquired(String phase, String variant, String stage_name, String node, Integer allocation_id) {}
  def on_node_released(String phase, String variant, String stage_name, String node, String build_result, Integer allocation_id) {}
  def on_hopic_installation_start(String node) {}
  def on_hopic_installation_end(String node) {}
  def on_node_workspace_preparation_start(String node) {}
  def on_node_workspace_preparation_end(String node, hopic) {}
  def on_locks_requested(List<String> locks) {}
  def on_locks_acquired(List<String> locks) {}
  def on_locks_released(List<String> locks) {}
  def on_submitting_build(boolean is_submitting) {}

  /**
  * Gets called at the start of a phase
  * This function is NOT called on a node!
  *
  * @param phase      The phase where the variant is part of
  */
  def on_phase_start(String phase) {}

  /**
  * Gets called at the end of a variant
  * This function is NOT called on a node!
  *
  * @param phase      The phase where the variant is part of
  */
  def on_phase_end(String phase) {}
} 
