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

package com.tomtom.hopic

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
  * @parent_phase     First phase of the chain when multiple variant are chained via wait_on_full_previous_phase no, null in case it is first phase of the chain.
  */
  def on_variant_start(String phase, String variant, String parent_phase) {}

  /**
  * Gets called at the end of a variant
  * This function is called on a node!
  *
  * @param phase      The phase where the variant is part of
  * @param variant    The variant name
  * @param exception  Exception from the build or null when there is no exception
  */
  def on_variant_end(String phase, String variant, exception) {}

  /**
  * Gets called just before a request for a node
  * This function is NOT called on a node!
  *
  * @param phase          The phase where the variant is part of
  * @param variant        The variant name
  * @param stage_name     The Jenkins full stage name
  * @param node_request   Expression that is being used when asking for a node
  * @param allocation_id  Hopic tracking id to match other on_node_ calls
  */
  def on_node_requested(String phase, String variant, String stage_name, String node_request, Integer allocation_id) {}
  
  /**
  * Gets called when a node is allocated
  * This function is called on the node that is being allocated!
  *
  * @param phase          The phase where the variant is part of
  * @param variant        The variant name
  * @param stage_name     The Jenkins full stage name
  * @param node           Name of the current executing node
  * @param allocation_id  Hopic tracking id to match other on_node_ calls
  */
  def on_node_acquired(String phase, String variant, String stage_name, String node, Integer allocation_id) {}
  
  /**
  * Gets called just before releasing the node
  * This function is called on a node!
  *
  * @param phase          The phase where the variant is part of
  * @param variant        The variant name
  * @param stage_name     The Jenkins full stage name
  * @param node           Name of the current executing node
  * @param build_result   Result of current build, values can be SUCCESS, UNSTABLE, FAILURE, NOT_BUILT, ABORTED (see https://javadoc.jenkins-ci.org/hudson/model/Result.html)
  * @param allocation_id  Hopic tracking id to match other on_node_ calls
  */
  def on_node_released(String phase, String variant, String stage_name, String node, String build_result, Integer allocation_id) {}

  /**
  * Gets called when hopic installation starts (before the creation of the virtualenv)
  * This function is called on a node!
  *
  * @param node Name of the current executing node
  */
  def on_hopic_installation_start(String node) {}
  
  /**
  * Gets called when hopic installation finished (after pip install) 
  * This function is called on a node!
  *
  * @param node Name of the current executing node
  */  
  def on_hopic_installation_end(String node) {}

  /**
  * Gets called before any Hopic workspace modification (before the hopic checkout-source-tree)
  * This function is called on a node!
  *
  * @param node Name of the current executing node
  */
  def on_node_workspace_preparation_start(String node) {}

  /**
  * Gets called after Hopic applied any required change to the workspace, including extension installation
  * This function is called on a node!
  *
  * @param node Name of the current executing node
  */
  def on_node_workspace_preparation_end(String node, hopic) {}
  
  /**
  * Gets called before build locks are being requested
  * This function is NOT called on a node!
  *
  * @param locks List of the requested locks
  */
  def on_locks_requested(List<String> locks) {}

  /**
  * Gets called after build locks are acquired
  * This function is NOT called on a node!
  *
  * @param locks List of the requested locks
  */
  def on_locks_acquired(List<String> locks) {}
  
  /**
  * Gets called after build locks are released
  * This function is NOT called on a node!
  *
  * @param locks List of the requested locks
  */
  def on_locks_released(List<String> locks) {}

  /**
  * Gets called when it is being determined if the build is submitting or not
  * This function is NOT called on a node!
  *
  * @param is_submitting boolean indicating if the build is submitting or not
  */
  def on_submitting_build(boolean is_submitting) {}

  /**
  * Gets called at the start of the phase
  * Phases that are chained with a previous phase will NOT get this callback
  * This function is NOT called on a node!
  *
  * @param phase      The phase where the variant is part of
  */
  def on_phase_start(String phase) {}

  /**
  * Gets called at the end of the phase
  * Phases that are chained with a previous phase will NOT get this callback
  * This method only gets called when the full chain of phases is executed 
  * This function is NOT called on a node!
  *
  * @param phase      The phase where the variant is part of
  */
  def on_phase_end(String phase) {}
} 
