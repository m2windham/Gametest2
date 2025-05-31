from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

def populate_tree_from_scene(tree_widget: QTreeWidget, scene_data: dict):
    """
    Populate the QTreeWidget with Blender scene data.
    scene_data: Nested dict/list representing the scene hierarchy.
    """
    tree_widget.clear()
    def add_items(parent, node):
        item = QTreeWidgetItem([node.get('name', '')])
        parent.addChild(item)
        for child in node.get('children', []):
            add_items(item, child)
    # Root node
    root_item = QTreeWidgetItem([scene_data.get('name', 'Scene')])
    tree_widget.addTopLevelItem(root_item)
    for child in scene_data.get('children', []):
        add_items(root_item, child)
    tree_widget.expandAll() 