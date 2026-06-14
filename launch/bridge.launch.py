from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    params = PathJoinSubstitution([
        FindPackageShare('ros2_odoo_bridge'), 'config', 'params.yaml'
    ])

    bridge = Node(
        package='ros2_odoo_bridge',
        executable='bridge_node',
        name='odoo_bridge',
        output='screen',
        parameters=[params],
    )

    return LaunchDescription([bridge])
