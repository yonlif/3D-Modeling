from typing import Tuple

import numpy as np
import pyrealsense2 as rs2


def calculate_pc_adjustments_by_pcs(object_pc: np.ndarray, captured_pc: np.ndarray) -> Tuple[float, float, float]:
    """

    :param object_pc: The object's known point cloud
    :param captured_pc: The captured point cloud
    :return: The deviation in x,y,z axis from the known point cloud
    """
    assert len(object_pc.shape) == 2
    assert len(captured_pc.shape) == 2
    assert object_pc.shape[1] == 3
    assert captured_pc.shape[1] == 3

    mins_obj = np.min(object_pc, axis=0)
    mins_cap = np.min(captured_pc, axis=0)
    maxs_obj = np.max(object_pc, axis=0)
    maxs_cap = np.max(captured_pc, axis=0)

    x_shift = ((maxs_obj[0] - maxs_cap[0]) + (mins_obj[0] - mins_cap[0])) / 2
    y_shift = (maxs_obj[1] - maxs_cap[1])
    z_shift = np.average(object_pc, axis=0)[2] - np.average(captured_pc, axis=0)[2]

    return x_shift, y_shift, z_shift


def calculate_pc_adjustments_by_frames(object_frame: np.ndarray, captured_frame: np.ndarray, intrinsics: rs2.intrinsics,
                                       shape: str = 'flat') -> Tuple[float, float, float]:
    """
    The frames should contain only the pixel relevant to the object, any non relevant should be 0.
    Note: I assume the function can be more efficient because, there's no need to average to object values.
    :param object_frame: The object's known depth frame
    :param captured_frame: The captured depth frame
    :param intrinsics: The intrinsics of the camera that captured the frame
    :param: surface: The shape of the object used to calibrate
    :return: The deviation in x,y,z axis from the known point cloud
    """
    assert len(object_frame.shape) == 2
    assert len(captured_frame.shape) == 2

    x_shift = ((find_edge_mean(object_frame, 'right', intrinsics)
                - find_edge_mean(captured_frame, 'right', intrinsics))
               + (find_edge_mean(object_frame, 'left', intrinsics)
                  - find_edge_mean(captured_frame, 'left', intrinsics))) / 2

    y_shift = (find_edge_mean(object_frame, 'up', intrinsics) - find_edge_mean(captured_frame, 'up', intrinsics))

    z_shift = average_z_frame(object_frame, shape) - average_z_frame(captured_frame, shape)

    return x_shift, y_shift, z_shift


def find_edge_mean(frame: np.ndarray, edge: str, intrinsics: rs2.intrinsics) -> float:
    """

    :param frame: The frame to find an edge on
    :param edge: The edge to find (right, left, up, down)
    :param intrinsics: The intrinsics of the camera that captured the frame
    :return: The average value of that edge - right/left would give the average x value,
            Similarly up/down would give the average y value
    """
    assert len(frame.shape) == 2

    vals = []

    if edge == 'right':
        for i in range(frame.shape[0]):
            for j in range(frame.shape[1]):
                if frame[i][j] != 0:
                    vals.append(rs2.rs2_deproject_pixel_to_point(intrinsics, [j, i], frame[i][j])[0])
                    break
    elif edge == 'left':
        for i in range(frame.shape[0]):
            for j in range(frame.shape[1] - 1, -1, -1):
                if frame[i][j] != 0:
                    vals.append(rs2.rs2_deproject_pixel_to_point(intrinsics, [j, i], frame[i][j])[0])
                    break
    elif edge == 'up':
        for j in range(frame.shape[1]):
            for i in range(frame.shape[0]):
                if frame[i][j] != 0:
                    vals.append(rs2.rs2_deproject_pixel_to_point(intrinsics, [j, i], frame[i][j])[1])
                    break
    elif edge == 'down':
        for j in range(frame.shape[1]):
            for i in range(frame.shape[0] - 1, -1, -1):
                if frame[i][j] != 0:
                    vals.append(rs2.rs2_deproject_pixel_to_point(intrinsics, [j, i], frame[i][j])[1])
                    break
    else:
        raise Exception(f'\'{edge}\' is not an edge')

    return np.average(vals)


def convert_pc_to_frame(pc: np.ndarray, intrinsics: rs2.intrinsics) -> np.ndarray:
    """

    :param pc: A point cloud
    :param intrinsics: The intrinsics of the camera that the object should be 'captured' from
    :return: The frame that would give that point cloud if it was captured from that camera
    """
    assert len(pc.shape) == 2
    assert pc.shape[1] == 3

    frame = np.zeros(shape=(intrinsics.width, intrinsics.height))

    for (x, y, z) in pc:
        j, i = rs2.rs2_project_point_to_pixel(intrinsics, [x, y, z])
        # Maybe should use int(i),int(j) because of collisions
        i, j = round(i), round(j)
        frame[i][j] = z

    return frame


def average_z_frame(frame: np.ndarray, shape: str = 'flat') -> float:
    """

    :param frame: A frame of a calibration object
    :param: surface: The shape of the object used to calibrate - flat, cylinder
    :return: The average z value of the frame, taken to account the of the object
    """

    assert len(frame.shape) == 2

    if shape == 'flat':
        vals = []
        for row in frame:
            for z in row:
                vals.append(z)
        return np.average(vals)
    elif shape == 'cylinder':
        vals = []
        for i in range(frame.shape[0]):
            left = right = -1
            for j in range(frame.shape[1] / 2):
                if frame[i][j] != 0 and left == -1:
                    left = j
                elif frame[i][frame.shape[1] - 1 - j] != 0 and right == -1:
                    right = j
            vals.append(frame[i][(left + right) / 2])
        return np.average(vals)
    else:
        raise Exception(f'\'{shape}\' is not a shape')


def generate_frame_flat_surface(width: float, height: float, distance: float, intrinsics: rs2.intrinsics) -> np.ndarray:
    """

    :param width: The width of the flat surface in meters
    :param height: The height of the flat surface in meters
    :param distance: The distance of the object from the camera
    :param intrinsics: The intrinsics of the camera that the object should be 'captured' from
    :return: The frame that should have been captured if that surface was captured from that camera from that distance
    """
    frame = np.zeros(shape=(intrinsics.height, intrinsics.width))

    max_j, max_i = rs2.rs2_project_point_to_pixel(intrinsics, [width / 2, height / 2, distance])
    max_i, max_j = int(min(max_i, intrinsics.height)), int(min(max_j, intrinsics.width))
    for i in range(intrinsics.height - max_i, max_i):
        for j in range(intrinsics.width - max_j, max_j):
            frame[i][j] = distance
    return frame
