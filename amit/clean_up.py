import functools
import json
import multiprocessing as mp
from math import cos, radians, sin, atan, degrees
from typing import List, Iterable, Tuple, Callable, Dict, Any

import numpy as np
import open3d as o3d
import pyrealsense2 as rs2


def capture_frames(config: rs2.config, number_of_frames: int = 1, dummy_frames: int = 0) -> List[rs2.depth_frame]:
    """
    Capture frames via a certain config.
    :param config: The config to capture the frames from
    :param number_of_frames: The number of frames to capture
    :param dummy_frames: The number of dummy frames to capture before capturing (If that makes sense lol)
    :return: A list of the frames that have been captured
    """

    pipe: rs2.pipeline = rs2.pipeline()

    pipe.start(config)
    try:
        for _ in range(dummy_frames):
            pipe.wait_for_frames()

        frames = []
        for _ in range(number_of_frames):
            frames.append(pipe.wait_for_frames().get_depth_frame())

        return frames
    finally:
        pipe.stop()


def apply_filters(frames: Iterable[rs2.depth_frame], filters: Iterable[rs2.filter],
                  after_filter: Iterable[rs2.filter]) -> rs2.frame:
    """
    Apply a set of filters on the frames that were captured.
    :param frames: The frames to filter
    :param filters: Filters to add to all the frames
    :param after_filter: filter to add to the final resulting frame
    :return: The final frame after adding filters
    """

    for frame in frames:
        for fil in filters:
            frame = fil.process(frame)

    for fil in after_filter:
        frame = fil.process(frame)

    return frame


def change_coordinates_inplace(points: np.ndarray,
                               f: Callable[[Tuple[float, float, float]], Tuple[float, float, float]]) -> None:
    """
    Change the coordinates of every point inplace via a function that returns the new coordinate of each point.
    :param points: The point cloud to change the coordinates of
    :param f: The function that the coordinates will change by
    :return: None
    """

    assert len(points.shape) == 2
    assert points.shape[1] == 3

    for i, v in enumerate(points):
        res_point = f(v)
        for j in range(points.shape[1]):
            points[i][j] = res_point[j]


def rotate_point_cloud_inplace(points: np.ndarray, angle: float) -> None:
    """
    Rotate a given point cloud around the y axis with a given angle inplace.
    The way to rotate a vertex is with matrix multiplication of (4x4) * (4x1) = (4x1):
    (cos(alpha)   0   -sin(alpha) 0) * (x) = (new x)
    (0            1   0           0) * (z) = (new z)
    (sin(alpha)   0   cos(alpha)  0) * (y) = (new y)
    (0            0   0           1) * (1) = (1    )
    :param points: The original point cloud, as a numpy array.
    :param angle: The angle, in degrees, to rotate the point cloud around the y axis, as an int.
    """

    assert len(points.shape) == 2
    assert points.shape[1] == 3

    # Create the left matrix in the multiplication
    rotate_matrix = np.array(
        [[cos(radians(angle)), 0, -sin(radians(angle)), 0],
         [0, 1, 0, 0],
         [sin(radians(angle)), 0, cos(radians(angle)), 0],
         [0, 0, 0, 1]])

    for i, (x, y, z) in enumerate(points):
        # Create an extended vector to perform the matrix multiplication
        ext_vec = np.array([[x], [y], [z], [1]])

        # Multiply the matrices (rotate the vertex)
        res_vec = np.dot(rotate_matrix, ext_vec)

        # Assign the result
        for j in range(points.shape[1]):
            points[i][j] = res_vec[j]


def angle_limit(points: np.ndarray, left_hor_bound: float = float('-Inf'), right_hor_bound: float = float('Inf'),
                bottom_ver_bound: float = float('-Inf'), top_ver_bound: float = float('Inf')) -> np.ndarray:
    """
    Limits the range of the points in a point cloud by angle.
    :param points: The point cloud to filter points out of
    :param left_hor_bound: Left horizontal angle bound (negative value)
    :param right_hor_bound: Right horizontal angle bound (positive value)
    Probably abs(left_hor_bound) = abs(right_hor_bound).
    :param bottom_ver_bound: The bottom vertical angle bound
    :param top_ver_bound: The upper vertical angle bound
    :return: A new point with only the points withing the bounds
    """

    assert len(points.shape) == 2
    assert points.shape[1] == 3

    del_idx = []

    for i, (x, y, z) in enumerate(points):
        hor_angle = degrees(atan(x / z))
        ver_angle = degrees(atan(y / z))
        if not left_hor_bound <= hor_angle <= right_hor_bound or not bottom_ver_bound <= ver_angle <= top_ver_bound:
            del_idx.append(i)

    return np.delete(points, del_idx, axis=0)


def get_filter(filter_name: str, *args) -> rs2.filter_interface:
    """
    Basically a factory for filters (Maybe change to Dict 'cause OOP)
    :param filter_name: The filter name
    :param args: The arguments for the filter
    :return: A filter which corresponds to the filter name with it's arguments
    """
    if filter_name == 'decimation_filter':
        return rs2.decimation_filter(*args)
    elif filter_name == 'threshold_filter':
        return rs2.threshold_filter(*args)
    elif filter_name == 'disparity_transform':
        return rs2.disparity_transform(*args)
    elif filter_name == 'spatial_filter':
        return rs2.spatial_filter(*args)
    elif filter_name == 'temporal_filter':
        return rs2.temporal_filter(*args)
    elif filter_name == 'hole_filling_filter':
        return rs2.hole_filling_filter(*args)
    else:
        raise Exception(f'The filter \'{filter_name}\' does not exist!')


def generate_adapted_point_cloud(camera_map: Dict[str, Any], software_map: Dict[str, Any],
                                 hardware_map: Dict[str, Any] = None) -> np.ndarray:
    """
    Generate a point cloud and adapts (e.g rotates it, invert, add filters after capturing etc..)
    :param camera_map: The settings for the camera: serial, angle, distance
    :param software_map: The software settings: number of frame/dummy frames
    :param hardware_map: The settings for the hardware in general: [fov restrictions or something like that?;
    unused for now]
    :return: The point cloud generated after all the calculations
    """

    config: rs2.config = rs2.config()
    config.enable_stream(rs2.stream.depth)
    config.enable_device(camera_map['serial'])

    # Convert filter names and args to filter objects
    filters = [get_filter(filter_name, *args) for (filter_name, *args) in software_map['filters']]
    after_filters = [get_filter(filter_name, *args) for (filter_name, *args) in software_map['after_filters']]

    frames = capture_frames(config, software_map['frames'], software_map['dummy_frames'])
    frame = apply_filters(frames, filters, after_filters)

    pc: rs2.pointcloud = rs2.pointcloud()
    points: np.ndarray = np.array(pc.calculate(frame).get_vertices())
    # Convert to viable np.ndarray, and also filter 'zero' points
    points = np.array([(x, y, z) for (x, y, z) in points if not x == y == z == 0])

    # Add angle restrictions, for now this is empty
    points = angle_limit(points)

    # Invert x, y axis and mirror the z axis around the distance line (Can add deviation adjustment)
    change_coordinates_inplace(points,
                               lambda p: (-p[0], -p[1], camera_map['distance'] - p[2]))
    rotate_point_cloud_inplace(points, camera_map['angle'])
    return points


def save_file(points: np.ndarray, filename: str) -> None:
    """
    Save a point cloud to a file
    :param points: The point cloud to save to a file
    :param filename:The filename
    :return: None
    """

    o3d_pc = o3d.geometry.PointCloud()

    o3d_pc.points = o3d.utility.Vector3dVector(points)
    o3d_pc.estimate_normals()

    distances = o3d_pc.compute_nearest_neighbor_distance()
    avg_dist = np.mean(distances)
    radius = 1.5 * avg_dist

    mesh = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
        o3d_pc,
        o3d.utility.DoubleVector([radius, radius * 2]))

    o3d.io.write_triangle_mesh(filename, mesh)


if __name__ == '__main__':
    with open('config.json') as f:
        cfg_map = json.load(f)

    cams = cfg_map['cameras']

    # Using processes means effectively side-stepping the Global Interpreter Lock
    with mp.Pool(processes=len(cams)) as pool:
        gen_pc = functools.partial(generate_adapted_point_cloud, software_map=cfg_map['software'],
                                   hardware_map=cfg_map['hardware'])
        pcs: List[np.ndarray] = pool.map(gen_pc, cams)

    save_file(np.concatenate(pcs), cfg_map['software']['filename'])

# TODO: Add debug mode/logging option
