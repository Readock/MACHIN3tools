import bpy
import bmesh
from bpy.props import EnumProperty, BoolProperty
import gpu
from gpu_extras.batch import batch_for_shader
import bgl
from .. utils.graph import get_shortest_path
from .. utils.ui import wrap_cursor


modeitems = [("MERGE", "Merge", ""),
             ("CONNECT", "Connect Paths", "")]

mergetypeitems = [("LAST", "Last", ""),
                  ("CENTER", "Center", ""),
                  ("PATHS", "Paths", "")]

pathtypeitems = [("TOPO", "Topo", ""),
                 ("LENGTH", "Length", "")]


class SmartVert(bpy.types.Operator):
    bl_idname = "machin3.smart_vert"
    bl_label = "MACHIN3: Smart Vert"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name="Mode", items=modeitems, default="MERGE")
    mergetype: EnumProperty(name="Merge Type", items=mergetypeitems, default="LAST")
    pathtype: EnumProperty(name="Path Type", items=pathtypeitems, default="TOPO")

    slideoverride: BoolProperty(name="Slide Override", default=False)

    wrongselection = False

    @classmethod
    def poll(cls, context):
        if context.mode == 'EDIT_MESH' and tuple(context.scene.tool_settings.mesh_select_mode) == (True, False, False):
            bm = bmesh.from_edit_mesh(context.active_object.data)
            return [v for v in bm.verts if v.select]

    def draw(self, context):
        layout = self.layout

        column = layout.column()

        if not self.slideoverride:
            row = column.split(factor=0.3)
            row.label(text="Mode")
            r = row.row()
            r.prop(self, "mode", expand=True)

            if self.mode == "MERGE":
                row = column.split(factor=0.3)
                row.label(text="Merge")
                r = row.row()
                r.prop(self, "mergetype", expand=True)

            if self.mode == "CONNECT" or (self.mode == "MERGE" and self.mergetype == "PATHS"):
                if self.wrongselection:
                    column.label(text="You need to select exactly 4 vertices for paths.", icon="INFO")

                else:
                    row = column.split(factor=0.3)
                    row.label(text="Shortest Path")
                    r = row.row()
                    r.prop(self, "pathtype", expand=True)

    def draw_VIEW3D(self, args):
        shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
        shader.bind()

        bgl.glEnable(bgl.GL_BLEND)
        bgl.glDepthFunc(bgl.GL_ALWAYS)

        bgl.glLineWidth(3)
        shader.uniform_float("color", (0.5, 1, 0.5, 0.5))
        batch = batch_for_shader(shader, 'LINES', {"pos": self.coords}, indices=self.edge_indices)
        batch.draw(shader)

        bgl.glDisable(bgl.GL_BLEND)

    def modal(self, context, event):
        context.area.tag_redraw()

        if event.type == "MOUSEMOVE":
            self.mouse_x = event.mouse_region_x
            self.mouse_y = event.mouse_region_y

        events = ["MOUSEMOVE"]

        if event.type in events:
            wrap_cursor(self, context, event, x=True)

            offset_x = self.mouse_x - self.last_mouse_x

            divisor = 5000 if event.shift else 50 if event.ctrl else 500

            delta_x = offset_x / divisor
            self.distance += delta_x

            self.slide(context, self.distance)



        elif event.type in {'MIDDLEMOUSE'}:
            return {'PASS_THROUGH'}


        elif event.type in {'LEFTMOUSE', 'SPACE'}:
            bpy.types.SpaceView3D.draw_handler_remove(self.VIEW3D, 'WINDOW')
            return {'FINISHED'}


        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            self.cancel_modal()
            return {'CANCELLED'}

        self.last_mouse_x = self.mouse_x

        return {'RUNNING_MODAL'}

    def cancel_modal(self):
        bpy.types.SpaceView3D.draw_handler_remove(self.VIEW3D, 'WINDOW')

        bpy.ops.object.mode_set(mode='OBJECT')
        self.initbm.to_mesh(self.active.data)
        bpy.ops.object.mode_set(mode='EDIT')

    def invoke(self, context, event):
        if self.slideoverride:
            bm = bmesh.from_edit_mesh(context.active_object.data)
            verts = [v for v in bm.verts if v.select]

            if len(verts) > 1:
                self.active = context.active_object

                self.active.update_from_editmode()

                self.initbm = bmesh.new()
                self.initbm.from_mesh(self.active.data)

                self.mouse_x = self.last_mouse_x = event.mouse_region_x
                self.distance = 1

                self.slide(context, self.distance)

                args = (self, context)
                self.VIEW3D = bpy.types.SpaceView3D.draw_handler_add(self.draw_VIEW3D, (args, ), 'WINDOW', 'POST_VIEW')

                context.window_manager.modal_handler_add(self)
                return {'RUNNING_MODAL'}

        else:
            self.smart_vert(context)

        return {'FINISHED'}

    def execute(self, context):
        self.smart_vert(context)
        return {'FINISHED'}

    def smart_vert(self, context):
        active = context.active_object
        topo = True if self.pathtype == "TOPO" else False

        bm = bmesh.from_edit_mesh(active.data)
        bm.normal_update()
        bm.verts.ensure_lookup_table()

        verts = [v for v in bm.verts if v.select]


        if self.mode == "MERGE":

            if self.mergetype == "LAST":
                if len(verts) >= 2:
                    if self.validate_history(active, bm, lazy=True):
                        bpy.ops.mesh.merge(type='LAST')

            elif self.mergetype == "CENTER":
                if len(verts) >= 2:
                    bpy.ops.mesh.merge(type='CENTER')

            elif self.mergetype == "PATHS":
                self.wrongselection = False

                if len(verts) == 4:
                    history = self.validate_history(active, bm)

                    if history:
                        path1, path2 = self.get_paths(bm, history, topo)

                        self.weld(active, bm, path1, path2)
                        return

                self.wrongselection = True


        elif self.mode == "CONNECT":
            self.wrongselection = False

            if len(verts) == 4:
                history = self.validate_history(active, bm)

                if history:
                    path1, path2 = self.get_paths(bm, history, topo)

                    self.connect(active, bm, path1, path2)
                    return

            self.wrongselection = True

    def get_paths(self, bm, history, topo):
        pair1 = history[0:2]
        pair2 = history[2:4]
        pair2.reverse()

        path1 = get_shortest_path(bm, *pair1, topo=topo, select=True)
        path2 = get_shortest_path(bm, *pair2, topo=topo, select=True)

        return path1, path2

    def validate_history(self, active, bm, lazy=False):
        verts = [v for v in bm.verts if v.select]
        history = list(bm.select_history)

        if lazy:
            return history

        if len(verts) == len(history):
            return history
        return None

    def weld(self, active, bm, path1, path2):
        targetmap = {}
        for v1, v2 in zip(path1, path2):
            targetmap[v1] = v2

        bmesh.ops.weld_verts(bm, targetmap=targetmap)

        bmesh.update_edit_mesh(active.data)

    def connect(self, active, bm, path1, path2):
        for verts in zip(path1, path2):
            if not bm.edges.get(verts):
                bmesh.ops.connect_vert_pair(bm, verts=verts)

        bmesh.update_edit_mesh(active.data)

    def slide(self, context, distance):
        mx = self.active.matrix_world

        bpy.ops.object.mode_set(mode='OBJECT')

        bm = self.initbm.copy()

        history = list(bm.select_history)

        last = history[-1]
        verts = [v for v in bm.verts if v.select and v != last]

        self.coords = []
        self.edge_indices = []

        self.coords.append(mx @ last.co)

        for idx, v in enumerate(verts):
            v.co = last.co + (v.co - last.co) * distance

            self.coords.append(mx @ v.co)
            self.edge_indices.append((0, idx + 1))


        bm.to_mesh(self.active.data)

        bpy.ops.object.mode_set(mode='EDIT')
