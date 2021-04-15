from typing import List

import numpy as np

from l5kit.data import ChunkedDataset
from l5kit.data.filter import (filter_agents_by_frames, filter_agents_by_labels, filter_tl_faces_by_frames,
                               filter_tl_faces_by_status)
from l5kit.data.labels import PERCEPTION_LABELS
from l5kit.data.map_api import MapAPI, TLFacesColors
from l5kit.rasterization.box_rasterizer import get_ego_as_agent
from l5kit.rasterization.semantic_rasterizer import indices_in_bounds
from l5kit.sampling import get_relative_poses
from l5kit.visualization.visualiser.common import (AgentVisualisation, CWVisualisation, EgoVisualisation,
                                                   FrameVisualisation, LaneVisualisation, TrajectoryVisualisation)


# TODO: this should not be here (maybe a config?)
COLORS = {
    TLFacesColors.GREEN.name: "#33CC33",
    TLFacesColors.RED.name: "#FF3300",
    TLFacesColors.YELLOW.name: "#FFFF66",
    "PERCEPTION_LABEL_CAR": "#1F77B4",
    "PERCEPTION_LABEL_CYCLIST": "#CC33FF",
    "PERCEPTION_LABEL_PEDESTRIAN": "#66CCFF",
}


def _get_frame_trajectories(frames: np.ndarray, agents_frames: List[np.ndarray], track_ids: np.ndarray,
                            frame_index: int) -> List[TrajectoryVisualisation]:
    """Get trajectories (ego and agents) starting at frame_index.
    Ego's trajectory will be named ego_trajectory while agents' agent_trajectory

    :param frames: all frames from the scene
    :param agents_frames: all agents from the scene as a list of array (one per frame)
    :param track_ids: allowed tracks ids we want to build trajectory for
    :param frame_index: index of the frame (trajectory will start from this frame)
    :return: a list of trajectory for visualisation
    """

    traj_visualisation: List[TrajectoryVisualisation] = []
    # TODO: factor out future length
    agent_traj_length = 20
    for track_id in track_ids:
        # TODO this is not really relative (note eye and 0 yaw)
        pos, *_, avail = get_relative_poses(agent_traj_length, frames[frame_index: frame_index + agent_traj_length],
                                            track_id, agents_frames[frame_index: frame_index + agent_traj_length],
                                            np.eye(3), 0)
        traj_visualisation.append(TrajectoryVisualisation(xs=pos[avail > 0, 0],
                                                          ys=pos[avail > 0, 1],
                                                          color="blue",
                                                          legend_label="agent_trajectory",
                                                          track_id=int(track_id)))

    # TODO: factor out future length
    ego_traj_length = 100
    pos, *_, avail = get_relative_poses(ego_traj_length, frames[frame_index: frame_index + ego_traj_length],
                                        None, agents_frames[frame_index: frame_index + ego_traj_length],
                                        np.eye(3), 0)
    traj_visualisation.append(TrajectoryVisualisation(xs=pos[avail > 0, 0],
                                                      ys=pos[avail > 0, 1],
                                                      color="red",
                                                      legend_label="ego_trajectory",
                                                      track_id=-1))

    return traj_visualisation


def _get_frame_data(mapAPI: MapAPI, frame: np.ndarray, agents_frame: np.ndarray,
                    tls_frame: np.ndarray) -> FrameVisualisation:
    """Get visualisation objects for the current frame.

    :param mapAPI: mapAPI object (used for lanes, crosswalks etc..)
    :param frame: the current frame (used for ego)
    :param agents_frame: agents in this frame
    :param tls_frame: the tls of this frame
    :return: A FrameVisualisation object. NOTE: trajectory are not included here
    """
    ego_xy = frame["ego_translation"][:2]

    #################
    # plot lanes
    lane_indices = indices_in_bounds(ego_xy, mapAPI.bounds_info["lanes"]["bounds"], 50)
    active_tl_ids = set(filter_tl_faces_by_status(tls_frame, "ACTIVE")["face_id"].tolist())
    lanes_vis: List[LaneVisualisation] = []

    for idx, lane_idx in enumerate(lane_indices):
        lane_idx = mapAPI.bounds_info["lanes"]["ids"][lane_idx]

        lane_tl_ids = set(mapAPI.get_lane_traffic_control_ids(lane_idx))
        lane_colour = "gray"
        for tl_id in lane_tl_ids.intersection(active_tl_ids):
            lane_colour = COLORS[mapAPI.get_color_for_face(tl_id)]

        lane_coords = mapAPI.get_lane_coords(lane_idx)
        left_lane = lane_coords["xyz_left"][:, :2]
        right_lane = lane_coords["xyz_right"][::-1, :2]

        lanes_vis.append(LaneVisualisation(xs=np.hstack((left_lane[:, 0], right_lane[:, 0])),
                                           ys=np.hstack((left_lane[:, 1], right_lane[:, 1])),
                                           color=lane_colour))

    #################
    # plot crosswalks
    crosswalk_indices = indices_in_bounds(ego_xy, mapAPI.bounds_info["crosswalks"]["bounds"], 50)
    crosswalks_vis: List[CWVisualisation] = []

    for idx in crosswalk_indices:
        crosswalk = mapAPI.get_crosswalk_coords(mapAPI.bounds_info["crosswalks"]["ids"][idx])
        crosswalks_vis.append(CWVisualisation(xs=crosswalk["xyz"][:, 0],
                                              ys=crosswalk["xyz"][:, 1],
                                              color="yellow"))
    #################
    # plot ego and agents
    agents_frame = np.insert(agents_frame, 0, get_ego_as_agent(frame))

    # TODO: move to a function
    corners_base_coords = (np.asarray([[-1, -1], [-1, 1], [1, 1], [1, -1]]) * 0.5)[None, :, :]
    corners_m = corners_base_coords * agents_frame["extent"][:, None, :2]  # corners in zero
    s = np.sin(agents_frame["yaw"])
    c = np.cos(agents_frame["yaw"])
    rotation_m = np.moveaxis(np.array(((c, s), (-s, c))), 2, 0)

    box_world_coords = corners_m @ rotation_m + agents_frame["centroid"][:, None, :2]

    # ego
    ego_vis = EgoVisualisation(xs=box_world_coords[0, :, 0], ys=box_world_coords[0, :, 1],
                               color="red", center_x=agents_frame["centroid"][0, 0],
                               center_y=agents_frame["centroid"][0, 1])

    # agents
    agents_frame = agents_frame[1:]
    box_world_coords = box_world_coords[1:]

    agents_vis: List[AgentVisualisation] = []
    for agent, box_coord in zip(agents_frame, box_world_coords):
        label_index = np.argmax(agent["label_probabilities"])
        agent_type = PERCEPTION_LABELS[label_index]
        agents_vis.append(AgentVisualisation(xs=box_coord[..., 0],
                                             ys=box_coord[..., 1],
                                             color="#1F77B4" if agent_type not in COLORS else COLORS[agent_type],
                                             track_id=agent["track_id"],
                                             type=PERCEPTION_LABELS[label_index],
                                             prob=agent["label_probabilities"][label_index]))

    return FrameVisualisation(ego=ego_vis, agents=agents_vis, lanes=lanes_vis,
                              crosswalks=crosswalks_vis, trajectories=[])


def zarr_to_visualizer_scene(scene_dataset: ChunkedDataset, mapAPI: MapAPI,
                             with_trajectories: bool = True) -> List[FrameVisualisation]:
    """Convert a zarr scene into a list of FrameVisualisation which can be used by the visualiser

    :param scene_dataset: a scene dataset. This must contain a single scene
    :param mapAPI: mapAPI object
    :param with_trajectories: if to enable trajectories or not
    :return: a list of FrameVisualisation objects
    """
    if len(scene_dataset.scenes) != 1:
        raise ValueError(f"we can convert only a single scene, found {len(scene_dataset.scenes)}")

    frames = scene_dataset.frames
    agents_frames = filter_agents_by_frames(frames, scene_dataset.agents)
    tls_frames = filter_tl_faces_by_frames(frames, scene_dataset.tl_faces)

    frames_vis: List[FrameVisualisation] = []
    for frame_idx in range(len(frames)):
        frame = frames[frame_idx]
        tls_frame = tls_frames[frame_idx]

        # TODO: hardcoded threshold, it would be great to have a slider filtering on this
        agents_frame = agents_frames[frame_idx]
        agents_frame = filter_agents_by_labels(agents_frame, 0.1)

        frame_vis = _get_frame_data(mapAPI, frame, agents_frame, tls_frame)

        if with_trajectories:
            traj_vis = _get_frame_trajectories(frames, agents_frames, agents_frame["track_id"], frame_idx)
            frame_vis = FrameVisualisation(ego=frame_vis.ego, agents=frame_vis.agents,
                                           lanes=frame_vis.lanes, crosswalks=frame_vis.crosswalks,
                                           trajectories=traj_vis)
        frames_vis.append(frame_vis)

    return frames_vis