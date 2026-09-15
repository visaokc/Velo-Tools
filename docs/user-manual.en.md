# Velo Tools Tutorial Handbook

Version **1.7.1**. Learn one task at a time, in the same five areas as the add-on. The examples use invented objects, not a required character or downloadable project.

**New here?** Read [Install and your first project](#game-start), then [the five-stage workflow](#game-files). Already working? Choose the tab that contains the button you need.

[Vertex Group Tools](#vertex-groups) · [Mesh Tools](#mesh) · [Weight Tools](#weights) · [Material Tools](#materials) · [Game](#game)

The English UI currently calls Mesh Tools **Grid tool** and Weight Tools **Weight Tool**. These are the same tabs. Chinese Blender interfaces use the Simplified Chinese catalog. The tutorials distinguish actual buttons from explanations.

[中文教程](user-manual.zh-CN.md) · [Interactive handbook](manual.html) · [Releases](https://github.com/visaokc/Velo-Tools/releases)

<a id="vertex-groups"></a>
## Vertex Group Tools

A vertex group is a named list of points and their influence values. Changing its name changes which bone can find it; changing its weights changes how strongly the points follow that bone. This tab organizes names. Use Weight Tools to move the values themselves.

<!-- directory:start -->
- [Merge duplicate-name groups](#vg-merge)
- [Fill gaps and sort numeric groups](#vg-gaps)
- [Remove unused groups](#vg-unused)
- [Remove all groups and start over](#vg-remove-all)
- [Set up an MMD-to-game mapping](#vg-mmd-setup)
- [Match by position and review the result](#vg-mmd-match)
- [Choose the right one of the four rename buttons](#vg-mmd-rename)
- [Save, load and reuse mapping tables](#vg-table-save)
- [Rename groups between any two meshes](#vg-general)
- [Use centroid links and the unmatched list](#vg-review)
- [Click an overlay point or drag its mapping](#vg-pick)
- [Generate and save WWMI original bone names](#vg-wwmi-map)
- [Switch names or bind the WWMI skeleton](#vg-wwmi-bind)
<!-- directory:end -->

### Vertex Group Operations

<a id="vg-merge"></a>
#### Merge duplicate-name groups

Combine groups that share the name before a dot, such as **7**, **7.001**, and **7.003**. This is useful after joining meshes; it does not discover which unrelated bones mean the same thing.

**Location:** Vertex Group Tools → Vertex Group Operations → Merge Vertex Groups.

##### Steps
1. Save a copy of the project. In Object Mode, select only the meshes to clean.
2. Inspect their Vertex Groups in Object Data Properties. Confirm that dotted names really belong to the same bone.
3. Run Merge Vertex Groups. Inspect the resulting base group and test the corresponding bone.

##### Small example
Two sleeve meshes were joined. One used group **7**, the other **7.001**. Merging consolidates their assignments under **7**, so one bone can drive both sleeve pieces. Do not use this to combine **Arm.L** and **Arm.R**: their dots describe different sides, not accidental duplicates.

##### Check and recover
The dotted duplicates should disappear, with their influence transferred to the base name. Review overlapping weights rather than assuming they were normalized. If meaningful dotted names were combined, undo immediately. [Mapping tables](#vg-general) are the right tool for deliberate name correspondence.

<a id="vg-gaps"></a>
#### Fill gaps and sort numeric groups

Add missing empty numeric groups and put the list in numeric order. Empty placeholders can be necessary when an export format uses list positions as bone indices.

**Location:** Vertex Group Operations → Fill Gaps In Vertex Groups.

##### Steps
1. Select the intended meshes in Object Mode and confirm they use numeric group names.
2. Run Fill Gaps In Vertex Groups.
3. Inspect the group list and the assignments on a few vertices.

##### Small example
A mesh has groups **0, 4, 2**. The result is **0, 1, 2, 3, 4**. Groups **1** and **3** are empty: the operation did not invent missing arm or leg weights.

##### Check and recover
Existing weights should still belong to their intended groups. Do not run unused-group removal immediately afterward if those placeholders are required. This is not a conversion from local Component numbers to shared numbers; choose the correct [skeleton strategy](#game-wwmi-skeleton) instead.

<a id="vg-unused"></a>
#### Remove unused groups

Remove groups that do not carry a positive influence on the selected meshes. This reduces clutter, but it is different from deleting small weights or limiting influences per vertex.

**Location:** Vertex Group Operations → Remove Unused Vertex Groups.

##### Steps
1. Save the project and select only the meshes whose lists need cleaning.
2. Check whether your current import/export strategy needs empty numeric placeholders.
3. Run the action, then inspect the list and test an existing deformation.

##### Small example
A boot mesh inherited 200 group names from a whole body, but only ankle and foot groups have weights. Cleaning removes the unused entries without transferring the boot to another bone.

##### Check and recover
This action cannot recover weights that were missing before cleanup. If an exporter needs a complete numbered list afterward, use [Fill Gaps](#vg-gaps) deliberately. Keep this separate from [Normalize Selected Vertices](#weight-normalize), which changes values.

<a id="vg-remove-all"></a>
#### Remove all groups and start over

Delete every vertex group from the selected meshes. Use this only when intentionally discarding their current weighting, not as a routine export fix.

**Location:** Vertex Group Operations → Remove All Vertex Groups.

##### Steps
1. Save a separate project copy. Confirm the selection excludes the original reference body.
2. Run Remove All Vertex Groups on the replacement mesh.
3. Create or transfer the intended weights before trying to animate or export it.

##### Small example
A newly imported accessory has unrelated groups from another rig. Remove them from that accessory, then transfer the appropriate head or hand influence from a correctly weighted reference.

##### Check and recover
The list will be empty and bone deformation may stop even if an Armature modifier is still present. The button does not delete the armature itself. Undo or reopen the saved copy if you removed the reference weights as well.

### MMD mapping

<a id="vg-mmd-setup"></a>
#### Set up an MMD-to-game mapping

Build a dictionary such as **UpperArm.L → 23**. The left column is the MMD/source group name; the right column is the game-side identity expected by your project. A row does not transfer weight values.

**Location:** Vertex Group Tools → MMD ↔ Unified ID Mapping.

##### Steps
1. Choose the game in Game, and configure its export Component collection.
2. Set MMD Source to your named reference mesh and Target Component to the imported game mesh. Align the two meshes in the same space.
3. Set MMD Skeleton only if you also want the source bone names changed by a rename action.
4. Create/select a Mapping Table text block. Click Fill Rows from Source Object. Use plus/minus to add or remove individual rows; Clear Mapping Table clears the dictionary, not mesh weights.

##### Small example
Your sleeve uses **UpperArm.L**, while the original Component uses **23**. Put those objects in the two fields, then fill and match the table. The useful result is a reviewed row connecting the two identities, not a promise that every other row is correct.

##### Check and recover
The source selector is not an export whitelist: the active MMD table applies by group name to eligible meshes throughout the configured export collection. A red exact-name selector means that object is currently missing. Use X to clear it, or restore that exact name; a similarly named object is not substituted.

<a id="vg-mmd-match"></a>
#### Match by position and review the result

The matcher compares each group's weighted center: roughly, where that group's influence is concentrated. Nearby centers suggest a correspondence; they do not prove two bones have the same purpose.

**Location:** MMD ↔ Unified ID Mapping → Matching Position → Match by position → write to this table.

##### Steps
1. Start with Rest Position when both meshes have comparable undeformed shapes.
2. Use Pose Position only when their visible evaluated shapes are the useful comparison. Armature, ShapeKey and modifier deformation then affect both sides.
3. Click the match action. Inspect every target column, especially fingers, face, layered clothing and left/right pairs.
4. Use the visual review panel and correct uncertain rows manually. Matching again updates the table; merely changing the pose does not.

##### Small example
One arm is lowered in the source and raised in the target. First make their visible poses comparable, select Pose Position, then match. A link from the left wrist to the right wrist is a reason to correct the row, not to enlarge a distance threshold.

##### Check and recover
Matching does not apply modifiers or rewrite geometry. Unweighted and special helper groups can be absent from filled rows. This authoring matcher is distinct from the verified full-skin-weight matching used when generating [original bone-name sidecars](#vg-wwmi-map).

<a id="vg-mmd-rename"></a>
#### Choose the right one of the four rename buttons

These buttons act on two different meshes. “Source” and “target” describe the fields above, not whichever mesh happens to be selected in the viewport.

**Location:** MMD ↔ Unified ID Mapping → the two rows of rename/restore actions.

##### Steps
1. Review and save the mapping table before applying any rename.
2. Use the table below to choose the side and direction.
3. Inspect vertex-group names and, when supplied, the source armature. Test deformation before saving over your only copy.

| Action | What it does |
| --- | --- |
| Rename source to unified number | Renames MMD source groups to the right-column identities; can rename the supplied source bones too |
| Restore source to MMD name | Restores the source snapshot, including original group names, order and weights |
| Rename target to MMD name | Gives the target Component source-side names and mapping-table order |
| Rename target to unified number | Returns target groups to game identities, keeping current order |

##### Small example
To weight-paint a numeric imported Component using readable source names, use **Rename target to MMD name**. Do not click the source button: that would rename the reference instead.

##### Check and recover
Source restoration is not just a cosmetic reverse rename: it can restore earlier weights. Do not expect weight edits made after that snapshot to survive restoration. Export preprocessing already works on temporary copies; you do not need destructive permanent renames solely to make every export work.

<a id="vg-table-save"></a>
#### Save, load and reuse mapping tables

Keep a reviewed dictionary inside the blend or in an external text file. A saved table records correspondence, not meshes, textures or a full rig backup.

**Location:** MMD or General Mapping → Mapping Table, Built-in Text synchronization, Import/Export Mapping Text.

##### Steps
1. Create/select the intended text block at the top of the panel.
2. Click the arrow **to Built-in Text** after editing rows; save the blend.
3. If you edit the text in Blender's Text Editor, click the arrow **from Built-in Text** to reload its rows.
4. Use Export Mapping Text for a separate text file, and Import Mapping Text when reusing it. Check the currently selected table before replacing anything.

##### Small example
Save a table named **Sleeve mapping** with your project. In a second project, import its exported text and verify that the target numbers still refer to the same extracted character and skeleton mode.

##### Check and recover
Keep MMD and General tables separate; their selectors and working objects are independent. Blank or duplicate/conflicting rows need review. Importing an old table is not evidence that its numbers remain valid for a new dump. Export a backup before clearing or replacing a useful table.

### General mapping and visual checks

<a id="vg-general"></a>
#### Rename groups between any two meshes

General Vertex Group Mapping is the game-independent version: map one set of names to another without requiring MMD naming or numeric game groups.

**Location:** Vertex Group Tools → General Vertex Group Mapping.

##### Steps
1. Set Source Object, Target Object and optional Skeleton in this panel, not the MMD fields above.
2. Select Rest or Pose Position, fill rows from the source, then match by position.
3. Enable a maximum-distance limit when you know an appropriate distance at your model's scale. Correct rejected rows rather than accepting arbitrary distant matches.
4. Apply Source → Target names, Restore Source, Target → Source names, or Target → Target names as appropriate. Save the table.

##### Small example
A source glove has **finger_index_01** while your rig expects **Index1.L**. Align the glove and target hand, review that row, then rename the intended side. The glove's painted values remain values; this does not sample the target hand's weights.

##### Check and recover
A linked skeleton is for synchronized renaming. Actual pose-space comparison comes from each mesh's deformation stack. General mapping is not interchangeable with the active MMD export mapping. If several joints cluster together, fill those rows by knowledge and [inspect the links](#vg-review).

<a id="vg-review"></a>
#### Use centroid links and the unmatched list

Visual review draws connections between source and target group centers. It helps spot crossed sides and distant guesses before names are changed.

**Location:** The visual review panel below MMD or General Mapping; General Mapping also has an Unmatched List.

##### Steps
1. Finish a first mapping pass, then enable that panel's overlay.
2. Turn labels and unmatched target points on. Adjust the displayed link distance if the view is too cluttered.
3. Orbit around the model. Inspect wrists, shoulders, fingers and the centerline from more than one angle.
4. Correct rows, rerun matching only when intended, and inspect again. Disable the overlay when finished.

##### Small example
A left sleeve link crosses the torso to a right-arm group. Check the source/target names in that row and replace the target with the correct left-side identity. A short link by itself is not proof; overlapping garment layers can share centers.

##### Check and recover
MMD and General overlays avoid drawing on top of one another. In Pose Position, moving the pose updates the links but not saved assignments. Empty labels or an unmatched entry call for a row/object check, not weight normalization.

<a id="vg-pick"></a>
#### Click an overlay point or drag its mapping

The visible mapping points are interactive. A click inspects a group's weights; a drag edits the correspondence. These are different actions.

**Location:** MMD or General Mapping → enabled visual review overlay → 3D Viewport.

##### Steps
1. Enable the correct overlay after choosing its source and target meshes.
2. Click a group endpoint to enter Weight Paint for that mesh/group and inspect the influence.
3. Drag a source endpoint onto the correct target endpoint to write that mapping row.
4. Check the table afterward. Disable the overlay or leave the weight-paint session when finished.

##### Small example
A sleeve source point is linked to the wrong arm. Drag its endpoint onto the correct target, then inspect the updated table. Dragging an already claimed target to another target redirects its claims, so review every affected row.

##### Check and recover
Dropping a source onto empty space clears its target; it is not a drag-cancel gesture. A claimed target dropped onto empty space is left alone. During inspection the picker temporarily uses a zero-strength subtract brush and restores its saved brush state when the session ends. Review the brush before intentional painting; do not assume a click moved weights.

### WWMI original bone names

<a id="vg-wwmi-map"></a>
#### Generate and save WWMI original bone names

Turn hard-to-read numeric WWMI groups into a verified dictionary of original bone names. You need the matching unpacked model; Velo does not reconstruct bone names from numbers alone.

**Location:** Vertex Group Tools → WWMI Numeric ID ↔ Original Bone Name. Select Wuthering Waves in Game.

##### Steps
1. Set Unpack Folder to the matching .uemodel assets and Object Source Directory to the WWMI extraction.
2. Keep similarity and voxel-size defaults for the first attempt. Click Generate Mapping Table.
3. Review the result. Version 1.7.1 checks complete corresponding skin-weight rows and rejects ambiguous bone identities, not just distant meshes.
4. Click Save Matching Results to Source Directory. Keep **WWMI_MatchingResult.json** with the extracted source. Load Mapping Table from Source Directory restores that saved result.

##### Small example
A torso and sleeve share several bones whose influenced points overlap. The full weight pattern distinguishes identities that a center-only comparison could swap. A successful result can later supply names and the saved skeleton even without the original unpack folder.

##### Check and recover
Old v1 results must be regenerated as v2 from the correct unpacked assets. Changing a similarity threshold cannot prove an ambiguous bone identity. Do not resolve a failure by hand-copying rows from an unrelated Component or another character.

<a id="vg-wwmi-bind"></a>
#### Switch names or bind the WWMI skeleton

Use the saved dictionary to make an imported project easier to pose. Name switching affects selected meshes; the one-click bind action has the wider configured Component-collection scope.

**Location:** WWMI Numeric ID ↔ Original Bone Name.

##### Steps
1. Generate or load a valid mapping using the previous tutorial.
2. For a small change, select the intended mesh and use Switch to original name or Switch to numeric numbering.
3. Use Import Skeleton if you need only the armature. Confirm Mirror Skeleton matches your import orientation.
4. Use Bind Skeleton to Mod Mesh to process the configured WWMI Component collection and bind it. Optional .L/.R conversion renames only supported detected pairs.

##### Small example
First switch one imported sleeve to original names and confirm the arm groups. Then bind the complete Mod collection and rotate the upper-arm bone slightly to check that the sleeve follows correctly.

##### Check and recover
Adding a table row does not establish a verified identity. Ambiguous used mappings stop the operation. Keep the extraction, saved mapping and skeleton snapshot together. Return to the matching import/export numbering strategy before assuming a renamed mesh is export-ready.

<a id="mesh"></a>
## Mesh Tools

Prepare the editable objects: sculpt across separate pieces, organize materials and collections, and manage ShapeKeys. The material helpers here organize geometry; the separate Material Tools tab controls which textures the game receives.

<!-- directory:start -->
- [Sculpt several objects as one, then return the edits](#mesh-sculpt)
- [Give a replacement mesh its Component identity](#mesh-prefix)
- [Create same-named materials without losing texture wiring](#mesh-material-name)
- [Keep material names in sync after object renames](#mesh-auto-name)
- [Split by material and control empty ShapeKey cleanup](#mesh-split)
- [Merge objects that use the same image set](#mesh-merge)
- [Clean repeated ShapeKey contamination conservatively](#mesh-contamination)
- [Fill missing basic mesh data](#mesh-fill)
- [Apply selected modifiers on a mesh with ShapeKeys](#mesh-modifiers)
- [Convert vertex-color storage to Linear](#mesh-linear)
- [Generate smooth normals in TEXCOORD1](#mesh-octahedral)
- [Generate Endfield smooth normals in TEXCOORD4](#mesh-texcoord4)
- [Generate COLOR outline normals and opt in](#mesh-color-normal)
- [Plan material parts in a collection tree](#mesh-route-preview)
- [Split into planned collections, or group by texture](#mesh-route-apply)
- [Control same-name ShapeKeys across a collection](#mesh-shape-summary)
- [Give custom ShapeKeys safe Deform numbers](#mesh-shape-number)
<!-- directory:end -->

### Sculpt and material organization

<a id="mesh-sculpt"></a>
#### Sculpt several objects as one, then return the edits

Create a temporary joined object so a brush can cross the seam between separate pieces. The originals remain the destinations for the finished positions.

**Location:** Mesh Tools → Multi-Object Sculpt.

##### Steps
1. Save a copy, select the original meshes in Object Mode, and click Create Merged Object.
2. Sculpt the merged object using brushes that move existing vertices. Do not remesh, use Dyntopo, delete points or change the originals' topology during this workflow.
3. Choose Apply Merged Object Sculpt to transfer the result to the originals.
4. If the originals have ShapeKeys, choose Apply Merged Object Sculpt (Shape Keys): the position changes are also added to their keys.

##### Small example
A shirt consists of a torso and two sleeves. Create the merged sculpt object and smooth their meeting area. Apply normally for keyless pieces; use the Shape Keys action if the shirt also has a breathing key, then test that key.

##### Check and recover
Check each original separately and test its keys at 0 and 1. The workflow relies on vertex correspondence; a new vertex cannot be reliably sent back just because it looks close. Undo or reopen the saved copy if topology changed.

<a id="mesh-prefix"></a>
#### Give a replacement mesh its Component identity

A name beginning with **Component N** tells the exporter which extracted part the mesh belongs to. The number is a source identity, not a freely chosen organizational label.

**Location:** Mesh Tools → Material Tool → Add the Component prefix to the selected object.

##### Steps
1. Identify the intended Component in the imported reference and matching object source.
2. Select the replacement meshes and set the number beside the button.
3. Run the action and inspect the final object names. Keep them inside the configured export collection.

##### Small example
If your reference sleeve really belongs to Component 2, add number **2** to a mesh called **Sleeve**. The result identifies that mesh with Component 2. This does not make weights from an unrelated Component compatible.

##### Check and recover
Do not copy the example number blindly. With Auto Split by Material enabled, material prefixes can also affect routing. With it disabled, only the whole object's own Component name controls ownership. See [export scope](#game-scope).

<a id="mesh-material-name"></a>
#### Create same-named materials without losing texture wiring

Give each processed object a material that follows its name. This helps material-based splitting recognize its Component and keeps the project readable.

**Location:** Mesh Tools → Material Tool → Generate a material ball for selected objects.

##### Steps
1. Select the intended meshes in Object Mode.
2. Inspect the first effective material slot; this is the material whose existing wiring should be retained.
3. Run the action. Inspect object, mesh and material names, then verify the visible textures.

##### Small example
An object named **Component 2 Sleeve** still has material **Material.018**. Run the action to give its edited material a useful matching name while retaining its diffuse image and original-texture mappings.

##### Check and recover
The edited material gets original-name priority; a retained previous material may be named **Backup …**. Other users keep their material contents and assignments. DATA and OBJECT-linked slots are supported; do not manually switch link types to fix names first. Distinct later slots and face assignments are not a request to collapse all materials into one.

<a id="mesh-auto-name"></a>
#### Keep material names in sync after object renames

The small refresh-style toggle beside material generation watches later renames. It is off by default and is saved in the scene.

**Location:** Mesh Tools → Material Tool → icon beside Generate a material ball.

##### Steps
1. Set the current game's export Component collection.
2. Enable the icon toggle. It uses Blender's highlighted state when on.
3. Rename an existing mesh inside that collection or a child collection.
4. Check that the mesh and its material follow the new name.

##### Small example
Rename a single-material object from **Component 2 Sleeve** to **Component 2 LongSleeve**. Its naming follows automatically. A new duplicate is first recorded without being modified; a later rename triggers synchronization.

##### Check and recover
Only meshes with zero or one material slot are processed automatically. Multi-material meshes are deliberately skipped. Objects outside the export collection are not watched. For an existing mismatch that needs immediate repair, use [manual material generation](#mesh-material-name).

<a id="mesh-split"></a>
#### Split by material and control empty ShapeKey cleanup

Separate faces into objects according to material. The adjacent threshold controls whether a resulting piece keeps a ShapeKey that barely moves any of its vertices.

**Location:** Mesh Tools → Material Tool → Separate by material; ShapeKey Cleanup Threshold.

##### Steps
1. Save a copy and inspect each face's assigned material. Splitting follows assignments, not what the surface looks like.
2. Set the cleanup threshold. The default is **0.0001** in local coordinate distance; use a smaller value to preserve tiny intended movements.
3. Select the meshes and run Separate by material.
4. Inspect the pieces and their remaining keys. The split starts from Basis and leaves pieces on Basis.

##### Small example
A shirt mesh has sleeve and button materials. After splitting, a sleeve-only key can be removed from the button object if all button movement is below the threshold. A tiny intentional button movement needs a sufficiently smaller threshold.

##### Check and recover
The threshold can delete an effectively empty key on a piece; it does not repair an already contaminated key. A large threshold can discard real subtle deformation. Undo and lower it if necessary. Basis-first splitting prevents later joins from filling absent keys with the wrong active shape.

<a id="mesh-merge"></a>
#### Merge objects that use the same image set

Reduce object clutter by joining selected meshes whose materials use the same set of images. This is not image packing or material-slot deduplication.

**Location:** Mesh Tools → Material Tool → Merge according to textures.

##### Steps
1. Select at least two intended meshes in Object Mode.
2. Inspect all images used by their materials, not just their diffuse image.
3. Run the merge action, then inspect the resulting objects, material slots and ShapeKeys.

##### Small example
Three cloth pieces use the same diffuse and normal images, while a buckle uses a separate image. The cloth group can join; the buckle remains separate. Material slots are retained rather than flattened into one shader.

##### Check and recover
Untextured meshes are skipped instead of being thrown into one large group. The same diffuse with different additional images is not necessarily the same image set. Check final Component naming and real collection membership, especially before export.

<a id="mesh-contamination"></a>
#### Clean repeated ShapeKey contamination conservatively

Try to remove an exactly repeated, complete material-region displacement that was copied into unrelated ShapeKeys. This is a narrow repair heuristic, not a general “fix all ShapeKeys” action.

**Location:** Mesh Tools → Material Tool → Clean ShapeKey Contamination.

##### Steps
1. Save a copy and select the affected meshes in Object Mode.
2. Inspect the suspected shared deformation in several keys.
3. Run the action and read the protected-source, repaired and skipped counts.
4. Test every changed key. Undo if the repetition was intentional.

##### Small example
A self-contained **SleeveLift** key is correct, but the same whole-sleeve displacement also appears unchanged in several independently shaped keys. If the tool can identify the protected source and repeated regions across at least three keys, it can reset only those pure extra regions to Basis.

##### Check and recover
Different or superimposed movement is not approximately subtracted. Ambiguous patterns are skipped, no keys are deleted, and the adjacent cleanup threshold is not used. Shared/read-only meshes, unsuitable key relationships or excessive snapshot size can be skipped. Intentionally reused identical deformation can resemble corruption, so inspect the result rather than treating a repair count as proof.

### Mesh utilities

<a id="mesh-fill"></a>
#### Fill missing basic mesh data

Create missing **COLOR** and **TEXCOORD.xy** data so a new mesh has the expected basic layers. A blank UV is only a placeholder, not a finished texture unwrap.

**Location:** Mesh Tools → Material Tool → Utilities → Fill Missing Mesh Data.

##### Steps
1. Select the new meshes and inspect Object Data Properties.
2. Run Fill Missing Mesh Data.
3. Confirm the expected UV/color layers exist; unwrap and author them as needed.

##### Small example
A newly modeled belt has no UV and no color layer. The action creates an empty primary UV and black COLOR. You must still unwrap the belt before a diffuse image will display meaningfully.

##### Check and recover
This does not restore source-specific packed data, normals or bone weights. Check the target Component's actual layout before assuming these two layers are sufficient. Use the appropriate smooth-normal generator only when that layout expects it.

<a id="mesh-modifiers"></a>
#### Apply selected modifiers on a mesh with ShapeKeys

Apply a modifier even when Blender's ordinary Apply action refuses a mesh with ShapeKeys. The operation must produce compatible geometry for the keys.

**Location:** Mesh Tools → Utilities → Apply Modifiers For Object With Shape Keys.

##### Steps
1. Save a separate copy and make the intended mesh active.
2. Open the action's dialog and tick only the modifiers to apply.
3. Keep Don't include armature deformations enabled unless deliberately baking a pose.
4. Apply, then inspect the modifier stack and test all important keys.

##### Small example
You want to apply a Mirror modifier to a symmetric garment that already has expression-independent fit keys. Apply only Mirror and compare Basis plus each fit key on both sides.

##### Check and recover
Not every topology-changing stack is compatible across keys. The dialog warns when ShapeKey animation data such as drivers/keyframes will be lost; retain a project backup for that data. A reported error or broken key is not a successful bake. Use Undo or the saved copy.

<a id="mesh-linear"></a>
#### Convert vertex-color storage to Linear

Move COLOR/COLOR1 layers to the storage convention expected by the toolchain. This is a data-format conversion, not a way to choose a new visible color.

**Location:** Mesh Tools → Utilities → Convert Vertex Colors To Linear.

##### Steps
1. Save the project and select the relevant meshes.
2. Inspect which COLOR/COLOR1 layers came from import and which you painted.
3. Run the conversion only when your workflow calls for Linear storage.
4. Check the layers and export a comparison before applying it broadly.

##### Small example
A mesh brought from an older project uses the older color storage. Select only its duplicate, not the retained reference, convert it and compare exported color behavior under the same game lighting.

##### Check and recover
This does not generate missing outline normals or tell you what a game's channels mean. Do not recolor packed data just because it looks unusual in Blender. Keep the original version until the target shader's result is confirmed.

### UV and outline-normal data

<a id="mesh-octahedral"></a>
#### Generate smooth normals in TEXCOORD1

Store a smooth direction in a UV layer for shaders that expect that convention. Think of this as writing extra shading instructions, not moving a texture island.

**Location:** Mesh Tools → UV Tool → Smooth Normal - Octahedral UV.

##### Steps
1. Confirm the target shader/export layout expects this data.
2. Ensure each selected mesh already has its first UV layer; that UV defines the direction basis.
3. Back up existing **TEXCOORD1.xy**, then run the generator.
4. Export and inspect the outline, especially at seams.

##### Small example
A new low-poly accessory has a faceted outline in a compatible workflow. Generate the extra smooth direction on a copy and compare the silhouette before and after, checking both its outer edge and the original texture orientation.

##### Check and recover
The action creates or replaces TEXCOORD1.xy. It does not work as a universal outline fix for every game. Changing the primary UV orientation changes the encoded direction. Use the Endfield-specific actions below when its source layout expects a different carrier.

<a id="mesh-texcoord4"></a>
#### Generate Endfield smooth normals in TEXCOORD4

Some Endfield Components store smooth-normal X/Y in **TEXCOORD4.xy**. This generator prepares that representation for new geometry.

**Location:** Mesh Tools → UV Tool → Generate Smooth Normal TEXCOORD4.

##### Steps
1. Check the extracted Component's .fmt and retained reference layers.
2. Keep the original imported auxiliary data where possible. On the new mesh, prepare the primary **TEXCOORD.xy** UV.
3. Run the generator before joining the new part into the Component.
4. Inspect the outline in the exported game result.

##### Small example
A newly modeled cuff is joining an Endfield Component that uses TEXCOORD4 for outline normals. Generate the cuff's layer first, leaving the old sleeve's authored layer intact.

##### Check and recover
Exact-position duplicates are smoothed together without joining UV islands. Existing TEXCOORD4 data is overwritten. This cannot reproduce deleted original bake data bit-for-bit; do not run it over an entire original model merely to fill one new part.

<a id="mesh-color-normal"></a>
#### Generate COLOR outline normals and opt in

Use this only when the Endfield Component stores the compatible smooth-normal representation in packed COLOR. COLOR is a container: not every Component uses it for the same purpose.

**Location:** Mesh Tools → Vertex Color Tool → Generate Smooth Normal COLOR; Enable COLOR Outline Normals.

##### Steps
1. Verify the source layout and retained reference data, as in the previous tutorial.
2. Select the new geometry and generate its COLOR data. Generation enables the opt-in checkbox automatically.
3. For already authored compatible COLOR, enable the checkbox without recalculating.
4. Perform a full export to regenerate mod.ini, then review the outline.

##### Small example
Your new cuff uses a compatible COLOR layout. Generate before any manual painting. If you regenerate afterward, your painted COLOR is overwritten.

##### Check and recover
Imported COLOR defaults to the game's native control. An unchecked box does not block ordinary COLOR export or override a selector already enabled by the game. One opted-in exported part can activate compatible custom draws in the same Component; incompatible layouts and other protected paths remain native. This is not an outline-width slider or a universal transparency mask.

### Collection routing

<a id="mesh-route-preview"></a>
#### Plan material parts in a collection tree

Preview where material-separated pieces should go before actually splitting them. Collection rows are real Blender collections; material leaves represent planned groups, not necessarily existing objects.

**Location:** Mesh Tools → Split into Collections by Material.

##### Steps
1. Set the current game's export Component collection.
2. Click Refresh Material Groups.
3. Add child collections using plus, and select a material leaf.
4. Click the destination collection's import/assignment icon to move that planned group. Use the disclosure arrow to expand/collapse, not to assign.

##### Small example
A combined garment has cloth and trim materials. Make **Cloth** and **Trim** child collections, then assign each virtual leaf to its intended folder before splitting.

##### Check and recover
Removing a child collection returns planned groups to the parent; it is not a request to erase the mesh. Already-separated objects follow their actual collection membership, including Outliner moves. Refresh before trusting a stale preview. These actions can disable Ignore Nested Collections so the new children remain exportable.

<a id="mesh-route-apply"></a>
#### Split into planned collections, or group by texture

Turn the preview into real separated objects. The material action keeps material-based pieces; the texture action additionally tries to join pieces with the same image set into fewer objects.

**Location:** Split into Collections by Material → the two split actions.

##### Steps
1. Complete the [collection plan](#mesh-route-preview) and save a copy.
2. Choose Split by Material into Collections for separate material pieces.
3. Alternatively choose the texture/grouping action to reduce compatible object count.
4. Inspect actual objects in the Outliner and check names, materials and ShapeKeys.

##### Small example
Keep a cuff and sleeve separate for independent editing using the material split. If several trim pieces use exactly the same images and need the same destination, the texture variant can group them.

##### Check and recover
These are real authoring changes, unlike temporary export-time splitting. Basis-first preparation applies here too. After manual moves, physical ownership of separated pieces is authoritative; stale virtual leaves must not move them back to an old hidden collection.

### ShapeKey summary

<a id="mesh-shape-summary"></a>
#### Control same-name ShapeKeys across a collection

Gather identically named ShapeKeys into one list so a garment made of several objects can be adjusted together.

**Location:** Mesh Tools → ShapeKey Summary (by Collection).

##### Steps
1. Select the collection containing the relevant meshes.
2. Let the list scan, or click Force refresh after a structural change.
3. Move a row's value to change contributors with that name; edit its name to rename those contributors together.
4. Hover over **xN** to see exactly which objects contribute.

##### Small example
Body, shirt and belt all have **FitWide**. Its row shows x3. Moving the shared slider adjusts all three, so the belt is not left behind.

##### Check and recover
Equal names mean shared control here. Rename a key on an individual object first if it must remain independent. Existing numbered Deform rows sort first, followed by natural name order. This panel organizes Blender data; runtime export still needs the [game's ShapeKey options](#game-efmi-shapes).

<a id="mesh-shape-number"></a>
#### Give custom ShapeKeys safe Deform numbers

Number selected, still-unnumbered entries for the runtime workflow. Existing numbered entries are protected, and the lowest free valid number is used.

**Location:** ShapeKey Summary → row checkboxes → Automatically rename.

##### Steps
1. Set the correct game. For WWMI, set the current object source with valid Metadata.json first.
2. Refresh the collection list. Tick the entries you want numbered; Select All/Select None affects renameable entries only.
3. Run Automatically rename.
4. Inspect the resulting names, then enable the applicable game-side custom ShapeKey export.

##### Small example
In an EFMI project, Deform 1 and Deform 8 already exist. Selecting **CapeLift** can assign **Deform 2 CapeLift**, rather than unnecessarily starting at 9. WWMI also reserves its native ranges, so its next available number may be much higher.

##### Check and recover
The checkbox selects items to rename; it is not a visibility or export toggle. Old panel helper wording may say “unchecked”: follow the checkbox/action behavior described here. WWMI missing or invalid metadata cancels numbering instead of risking a native-ID collision. Do not borrow the EFMI example's number for a WWMI project.

<a id="weights"></a>
## Weight Tools

Weights are influence amounts: 0 means no pull, 1 means full pull for that group. A clean final vertex usually has a controlled total and influence count, but Velo intentionally separates copying from cleanup. Transfer first; normalize only after deciding which values may change.

<!-- directory:start -->
- [Choose the source, receiver and optional mirror](#weight-objects)
- [Choose Robust or surface interpolation](#weight-engine)
- [Transfer a group without silently rewriting the rest](#weight-transfer)
- [Smooth only when the copied field needs it](#weight-smooth)
- [Mirror the active group using explicit pairs](#weight-mirror)
- [Move one group's weights into another on the same mesh](#weight-group-move)
- [Consolidate several mapping sources that share a target](#weight-map-merge)
- [Normalize selected vertices and limit influences](#weight-normalize)
- [Repair only selected mirrored vertices](#weight-selected-mirror)
- [Diagnose failed transfers instead of tuning blindly](#weight-advanced)
<!-- directory:end -->

### Working objects and transfer

<a id="weight-objects"></a>
#### Choose the source, receiver and optional mirror

The source supplies one group's influence field. The target receives it. Neither is chosen implicitly from the last object you happened to click once explicit fields are set.

**Location:** Weight Tools → Working Objects.

##### Steps
1. Set Source Mesh to a correctly weighted reference. Choose Source Vertex Group; use the refresh icon after changing its group list.
2. Set Target Mesh to the garment or replacement that should receive weights.
3. Inspect Mirror Vertex Group. Set the intended opposite-side group, or clear it for a one-sided transfer.
4. Set Target Skeleton if needed for creating a missing receiving bone. Align the actual source and target surfaces.

##### Small example
To weight a sleeve, source is the reference arm, source group is the upper-arm influence, target is the sleeve. The sleeve is not the source merely because it is selected in the viewport.

##### Check and recover
Source candidates exclude locked/special groups. Exact-name object fields keep a missing name in red and reconnect when that same-named mesh returns. X clears it. A target field that stays red after a merge may need the actual new name; similar names are never guessed.

<a id="weight-engine"></a>
#### Choose Robust or surface interpolation

Both engines sample a source surface, but they handle gaps differently. Robust adds inpainting: it extends usable samples into areas where a direct surface match is missing.

**Location:** Weight Tools → Transfer settings → Transfer engine.

##### Steps
1. Try Robust for clothing with gaps or uneven overlap.
2. If the panel offers Install Robust dependencies, read the shown size and run that installation first. Wait for completion.
3. Choose Face interpolation pass for Blender's built-in nearest-face interpolation path when suitable.
4. Test one receiving group on a saved copy before processing a whole outfit.

##### Small example
A close-fitting sleeve is a straightforward surface-interpolation case. A sleeve with a loose cuff may benefit from Robust, provided enough reliable nearby samples remain to guide the missing area.

##### Check and recover
The bundled optional native package manifest targets Windows CPython 3.11; other Blender/Python environments are not automatically covered. These packages live outside the add-on and the standalone Robust add-on is not required. If installation or matching fails, inspect the full report. Changing the engine does not fix a wrong bone mapping or misaligned model.

<a id="weight-transfer"></a>
#### Transfer a group without silently rewriting the rest

Copy the sampled receiving field and optional strict mirror. Existing unrelated groups keep their values, even if the temporary total becomes greater or less than one.

**Location:** Weight Tools → Transfer settings → Execute weight transfer.

##### Steps
1. Complete [Working Objects](#weight-objects). With manual receiving name off, inspect the group inferred from the MMD mapping.
2. Enable Manually specify receiving group only to provide an explicit override. Inspect the mirror receiver too.
3. Unlock a receiving group that must be replaced. Reuse/clear controls are fixed: an existing unlocked receiver is reused and its old field is replaced.
4. Configure optional smoothing, then transfer. Auto-lock receiving groups can protect completed writes.
5. Repeat for other groups, and perform [manual cleanup](#weight-normalize) after the sequence.

##### Small example
A vertex has unrelated weight 0.8. The sampled new receiver is 0.6. Transfer keeps both, totaling 1.4; it does not shrink 0.6 to 0.2. You decide later which groups may be rescaled.

##### Check and recover
Donors and the group-count limit do not constrain source transfer. Missing/ambiguous mirror correspondences are reported and preserve the destination rather than averaging layers. A failed verified write rolls back memberships and locks. Creating a Blender bone does not by itself create a supported new game-runtime bone.

<a id="weight-smooth"></a>
#### Smooth only when the copied field needs it

Optional smoothing softens abrupt changes in the new receiving group before its final mirror is derived. It is not automatic whole-mesh cleanup.

**Location:** Weight Tools → Post-processing → Enable smoothing, repetitions and strength.

##### Steps
1. Start with smoothing off to see the engine's actual sampled result.
2. If it is too abrupt, enable smoothing and start with a low strength and few repetitions.
3. Unlock the receiver before repeating a transfer that previously auto-locked it.
4. Compare the joint bend and seam areas after transfer.

##### Small example
A sleeve's upper-arm influence has a harsh step near the elbow. Try strength **0.2** and a few repetitions on a copy, then compare against the unsmoothed transfer. These are trial values, not a setting for every model.

##### Check and recover
More repetitions can blur useful detail. UV seam edges block smoothing propagation, so a seam can remain a boundary. Smoothing does not normalize the other groups, and the checkbox alone does not repair already copied weights: it is used by the transfer operation.

### Mirror and group transfer

<a id="weight-mirror"></a>
#### Mirror the active group using explicit pairs

Mirror Weight is the standalone whole-group workflow for eligible Component meshes. It is different from the selected-vertex repair button below.

**Location:** Weight Tools → Mirror Mapping Groups.

##### Steps
1. Set the game's export collection and make the intended Component mesh active.
2. Choose its authoritative active group. Review automatic left/right detection.
3. Add manual pair rows when names or numeric groups do not express a reliable pair. Remove obsolete rows with minus.
4. Decide whether Normalize standalone mirror and the influence limit should apply, then run Mirror Weight.

##### Small example
Groups **12** and **18** are a known left/right pair in this particular source. Record that pair, make the healthy side active, and mirror it. Do not reuse those example numbers in another source without checking.

##### Check and recover
Standalone normalization preserves locked groups and uses eligible unlocked groups to distribute remaining capacity. Automatic donor count is a preference, not a list of the only permissible groups. When you only need to copy a few damaged points exactly, use [selected-vertex mirror](#weight-selected-mirror), which never normalizes.

<a id="weight-group-move"></a>
#### Move one group's weights into another on the same mesh

Add the source group's values to the target group, then clear the source values. This does not sample another mesh or merely change a name.

**Location:** Weight Tools → Weight Group Transfer.

##### Steps
1. Make the intended mesh active.
2. Choose Source Group on the left and a different existing Target group on the right.
3. Ensure the groups to be changed are not protected by locks.
4. Run Execute weight group transfer and inspect the full result.

##### Small example
A vertex has 0.2 in a redundant helper group and 0.5 in the intended parent group. After transfer, the parent has 0.7 and the helper has no assignment at that point.

##### Check and recover
Values above 1 are clipped on write and reported; this operation is not lossless when the sum exceeds 1. The source group can remain as an empty name. Undo if you chose the wrong direction, and test the joint before removing now-empty groups.

<a id="weight-map-merge"></a>
#### Consolidate several mapping sources that share a target

When several source groups genuinely mean one destination, merge their weights into the suitable parent source and remove redundant correspondence. This changes both source weighting and table rows.

**Location:** Weight Group Transfer → merge according to MMD mapping / general mapping.

##### Steps
1. Save the blend and export a backup of the relevant mapping table.
2. Review all rows pointing to the same target and the source armature hierarchy.
3. Choose the action for the table you actually configured: MMD or General.
4. Inspect retained parent groups, cleared/removed source groups and disconnected/pruned rows.

##### Small example
Two helper influences on a sleeve intentionally map to one game upper-arm identity. Consolidation keeps their combined influence under the selected family parent instead of leaving two competing rename destinations.

##### Check and recover
This acts on the source configured by that table, not any arbitrary active mesh. A mistaken many-to-one mapping should be corrected, not legitimized by merging. Retain the backup until deformation and export are confirmed.

### Cleanup and local repair

<a id="weight-normalize"></a>
#### Normalize selected vertices and limit influences

Redistribute editable existing weights proportionally after optional influence pruning. Locked groups keep their values; missing memberships are not invented.

**Location:** Weight Tools → Post-processing → Normalize selected vertices by proportion.

##### Steps
1. Finish the intended transfers, then enter Edit Mode on the target mesh.
2. Select only the vertices to clean.
3. Explicitly unlock groups whose values may change. Set Limit the number of vertex groups and the maximum if required by your target layout.
4. Run normalization and inspect per-vertex totals, influence count and deformation.

##### Small example
A protected group is locked at 0.6. Two editable existing groups contain 0.3 and 0.1. They already fit the remaining 0.4. If those editable values were 0.6 and 0.2, proportional cleanup can bring them back to 0.3 and 0.1 while keeping the lock.

##### Check and recover
The common default of four influences is not a universal game rule. An impossible locked-weight budget needs manual review; the tool does not silently unlock completed groups. Normalization cannot correct a wrong sampled bone or reverse a bad smoothing choice.

<a id="weight-selected-mirror"></a>
#### Repair only selected mirrored vertices

Copy healthy-side weights across the mesh's local X axis without normalization, influence limiting or geometry edits. Only the destination changes.

**Location:** Weight Tools → Post-processing → -X to +X / +X to -X → Mirror selected vertex weights.

##### Steps
1. Make shared mesh data single-user if necessary, then enter Edit Mode.
2. Select the healthy endpoints or only the damaged destination points. Both sides do not need selection.
3. Pick the direction using local X, not screen left/right. Ensure both groups in each intended pair are unlocked.
4. Run the action. F9 can adjust direction; Ctrl+Z undoes it.

##### Small example
The positive-X cuff has bad wrist weights. Select only those damaged points and choose **-X to +X**. The negative-X cuff is the source even though it was not selected.

##### Check and recover
Only existing reciprocal pairs participate; neutral groups copy under their own names. Unresolved numbers, missing pairs and ambiguous coincident layers are skipped. Locked groups, source values, other vertices, selection and ShapeKeys stay unchanged. Different totals caused by protected groups remain protected rather than “fixed.”

<a id="weight-advanced"></a>
#### Diagnose failed transfers instead of tuning blindly

Advanced settings control which surface samples are accepted. Read the report first so you change the parameter related to the failure.

**Location:** Weight Tools → Advanced; clickable last-result text and copy icon.

##### Steps
1. Open View full results. Check object names, receiving locks, missing mappings and dependency errors first.
2. For Robust distance failures, inspect actual model scale and alignment before adjusting Maximum Distance.
3. Review Normal Angle and Allow normal flipping for thin or opposite-facing surfaces.
4. Use deformed source/target only when their evaluated shapes are intended. Evaluated target topology must still match the original.

##### Small example
A cuff sits far from the source wrist because one object is misplaced. Moving it into alignment is the fix. Increasing the distance until the cuff samples the torso would merely hide the real problem.

##### Check and recover
Point inpaint changes the inpainting neighborhood model, not the bone identity. Limit dilation concerns protection during influence pruning, not the source transfer limit. Copy the full report for diagnosis, preserve the project and do not treat a short “finished” message as a deformation check.

<a id="materials"></a>
## Material Tools

Always distinguish two images: the **original** is the game's extracted texture you intend to replace; the **replacement** is your authored image. Connecting a pretty image in Blender is not enough to identify the correct game texture. This tab connects the two without asking you to type shader slot numbers.

<!-- directory:start -->
- [A complete first material: replace a sleeve's textures](#material-start)
- [Initialize selected materials safely](#material-initialize)
- [Connect the right image to each semantic input](#material-inputs)
- [Choose which original game texture a role replaces](#material-original)
- [Refresh originals without rebuilding your material](#material-refresh)
- [Configure once, then propagate by the same diffuse](#material-propagate)
- [Replace a synchronized texture from any member](#material-replace)
- [Detach one role, clear a mapping, or keep the game texture](#material-independent)
- [Enable independent material textures for export](#material-export)
- [Group draws by texture without losing required order](#material-batching)
- [Understand filenames and why an old output texture stays unchanged](#material-files)
<!-- directory:end -->

### Build one working material first

<a id="material-start"></a>
#### A complete first material: replace a sleeve's textures

Start with one material and prove the whole path before batching an outfit. This tutorial links the individual operations in the order a beginner needs them.

**Location:** Material Tools, then Game → the selected game's Export Mod.

##### Steps
1. Prepare a normal single-source EFMI or WWMI project and save a copy. Keep the extracted original textures in its object-source folder.
2. Select the sleeve and its intended active material. [Initialize](#material-initialize) it.
3. Connect a saved replacement diffuse and normal through the semantic inputs; [check their originals](#material-original).
4. In Export Mod, enable Auto Split by Material, Slot-style Textures and Use Material Textures. Keep the default template, full INI output and texture copying.
5. Export to a separate test output and inspect the result in game before [propagating](#material-propagate) to more materials.

##### Small example
**Sleeve_D.dds** is your new color image and **Sleeve_N.dds** your new normal. Each role points to the correct extracted original for the sleeve's Component. You do not put Sleeve_N into the original picker or type a guessed ps-t number.

##### Check and recover
The independent material layer currently excludes CrossIB, WWMI Cross-Scene, asset-name matching, custom/live templates and partial export. Ordinary Slot export supporting one of those workflows does not mean this extra material layer also supports it.

<a id="material-initialize"></a>
#### Initialize selected materials safely

Create the plugin's semantic texture-input shader while retaining recognizable diffuse-image and UV wiring. Original materials are kept as backups; this is not a conversion that preserves every arbitrary shader effect.

**Location:** Material Tools → Initialize Selected Materials.

##### Steps
1. Choose the correct game and object source. Initialization can run without a source, but original mapping will then need attention later.
2. In Object Mode, select only the meshes whose used materials should be initialized.
3. Run Initialize Selected Materials.
4. Inspect the Shader Editor: the semantic node's Shader output should reach the active Material Output.

##### Small example
An imported MMD sleeve has a recognizable diffuse texture but a complicated preview shader. Initialization gives you clear Diffuse/Normal/etc. inputs while preserving that diffuse's vector path. Check any special preview effects separately.

##### Check and recover
Unselected users keep their material contents and slot assignments. The edited replacement receives name priority; the retained original may become **Backup <name>** rather than taking the desired name away. Repeating initialization refreshes an already initialized material without rebuilding its connections. Undo or reassign the retained original to revert.

<a id="material-inputs"></a>
#### Connect the right image to each semantic input

Semantic inputs describe the image's job. They do not magically convert an image into that kind of map, and a Blender preview is only an approximation of the game's shader.

**Location:** Shader Editor → the initialized material's texture-input node.

##### Steps
1. Add/open an Image Texture for each saved image.
2. Connect its **Color output** directly, or through reroutes, to the appropriate semantic input.
3. Keep Shader connected to the active Material Output.
4. Save painted/edited images before export. Bake procedural results to supported files first.

| Input | Plain-language purpose |
| --- | --- |
| Diffuse | The main surface color |
| Normal | Small-scale surface direction; not a second color image |
| Packed PBR (Endfield) | Several material properties stored in one image |
| FTM (WWMI) | The game's packed material-control image |
| Mask | A control image whose meaning depends on the original shader |
| Emission | The image used for emissive appearance |
| Light Map / Detail | Additional shading/detail images when the source actually uses them |

##### Small example
Connect Sleeve_D Color to Diffuse and Sleeve_N Color to Normal. Do not insert a procedural mix and expect the exporter to reconstruct it. Alpha is preview control, not a separate exported texture role.

##### Check and recover
Packed PBR/FTM bytes are copied unchanged. Do not assume generic metallic/roughness channel meanings for an unknown game map. Saved DDS/PNG/JPG/JPEG/TGA/BMP and packed file images are supported without conversion; dirty, generated, animated, tiled or procedural images need saving/baking first.

### Match originals

<a id="material-original"></a>
#### Choose which original game texture a role replaces

The picker identifies the old texture, not your new image. A unique suggestion applies immediately; genuinely ambiguous candidates stay unassigned until you choose.

**Location:** Material Tools → the active material's role → Choose Original Texture.

##### Steps
1. Set the matching game object-source folder and refresh the source mapping.
2. Inspect the original filename shown under each connected role.
3. Click a wrong or unassigned role and choose the extracted original it is meant to replace.
4. Leave intentional non-replacements as Keep Game Texture. Verify every connected, enabled role before export.

##### Small example
Two extracted normal-like images appear for a sleeve: its main surface and a separate detail pass. Use retained reference/evidence to choose the main normal for Sleeve_N. Similar format or color alone is not enough.

##### Check and recover
Candidate hints use captured use, format, dimensions and naming. They are suggestions, not universal shader-slot rules. Manual choices remain authoritative; each Component resolves its own original and runtime destination. A red unresolved role is not permission to choose the first item just to make the red disappear.

<a id="material-refresh"></a>
#### Refresh originals without rebuilding your material

Re-read the live extracted-source catalog while retaining your authored image connections and manual choices. Top-level retained files determine which originals are currently available.

**Location:** Material Tools → Refresh Source Mapping.

##### Steps
1. Confirm the correct object-source folder, not the Mod output folder.
2. Keep desired original images directly in that folder. Move an intentionally excluded original to a backup subfolder rather than editing extraction JSON.
3. Run Refresh Source Mapping on the active material.
4. Inspect new unique assignments, preserved manual choices and roles marked Keep Game Texture.

##### Small example
A previously mapped shared mask should no longer receive this additional replacement. Move its retained original out of the source root and refresh. Its Blender node remains for preview, while that removed original is no longer automatically replaced. Returning the file permits its inferred mapping to recover.

##### Check and recover
Explicit manual Keep Game Texture remains an opt-out. Refresh is not a button to copy your new PNG into the game. Existing propagated mappings survive refresh; repeated edits keep the edited material's name instead of accumulating routine .001 suffixes.

### Propagation and persistent synchronization

<a id="material-propagate"></a>
#### Configure once, then propagate by the same diffuse

Copy other texture roles to selected materials that use the same diffuse image. Matching means the same image identity or canonical saved path, not two images that happen to look similar.

**Location:** Material Tools → Propagate by Same Diffuse.

##### Steps
1. Fully configure one source material. Select source and target meshes, with that source material active.
2. Confirm intended targets use the same diffuse. All selected material slots can contribute original-identity evidence; changed targets must be used by faces.
3. Run propagation with Replace Existing Connections off to fill empty inputs.
4. Inspect connected/cleared/mapping counts and unresolved roles. Turn overwrite on only if you intend to replace targets' populated non-diffuse roles too.

##### Small example
Five sleeve materials use one diffuse image. Configure one normal and emission, select all five and propagate. A target with its own independently edited normal keeps it in fill-only mode; its empty emission can be filled. Each Component still gets its own original mapping.

##### Check and recover
An active configured material takes priority; otherwise exactly one selected configured source is required. Overwrite also clears non-diffuse roles absent from the source. In fill-only mode, removal from a previously linked source propagates to selected still-linked members, but not independently changed populated roles. Empty image nodes and dangling reroutes are disconnected rather than exported as valid images.

<a id="material-replace"></a>
#### Replace a synchronized texture from any member

Propagation records explicit per-role membership. Later replacement uses those members, not a global search for every material that looks similar.

**Location:** Material Tools → role's replacement-path row → folder icon; Synced: N materials.

##### Steps
1. Click Synced: N materials to inspect exactly which object/material uses belong to that role.
2. Click the folder icon beside the replacement path and choose the new saved image.
3. Inspect all listed members, even those not selected in the viewport.
4. Save the blend; membership survives reopen and normal object/material/image renames.

##### Small example
After propagating a sleeve normal to five materials, select only one member and replace it with **Sleeve_N_v2.dds**. All five still-linked normal roles update. Their original game identities and UV wiring stay their own.

##### Check and recover
The path field is read-only information, not an original picker. Packed files show their recorded path, which need not exist externally. The tool swaps node images instead of globally repointing a shared Image datablock. A duplicate object is not automatically recruited; manual node edits become independent until deliberately propagated again.

<a id="material-independent"></a>
#### Detach one role, clear a mapping, or keep the game texture

These three controls solve different problems. None is a general “delete all linked images” button.

**Location:** Material Tools → each role's unlink, original-reset and original-picker controls.

##### Steps
1. Decide whether you want independent editing, a new original assignment, or no additional export replacement.
2. Use the matching action below.
3. Inspect the replacement path, original state and membership count separately.
4. Refresh/export only after those three states express your intent.

| Action | Original identity | Image connection | Synchronization |
| --- | --- | --- | --- |
| Leave This Texture Sync | Kept | Kept | Only this object/material role leaves |
| Clear Original Mapping | Unassigned; clears manual opt-out too | Kept | Kept |
| Keep Game Texture | Explicitly skips this extra replacement | Kept for preview | Kept, but replacement does not re-enable export |

##### Small example
One cuff needs a different normal: leave its normal sync, then replace that normal. If instead you want the game to keep its normal, choose Keep Game Texture. Clearing the original is not a permanent disable: refresh may infer it again.

##### Check and recover
Other roles and members stay linked. Keep Game Texture skips this material layer only; it does not remove other existing Component/Hash overrides. Undo reverses accidental changes; inspect the actual scope before editing a group.

### Export and verify

<a id="material-export"></a>
#### Enable independent material textures for export

This extra export layer lets different material draws replace the same original with different images. It requires safe Component/pass-aware Slot evidence.

**Location:** Game → EFMI/WWMI → Export Mod → Velo Compatibility Options.

##### Steps
1. Use an ordinary single-source project with current ShaderTextureUsage.json.
2. Enable Auto Split by Material, Slot-style Textures and Use Material Textures.
3. Choose Native Format Read with a compatible runtime, or the deliberate legacy Fuzzy mode. Keep full export, default template, INI writing and texture copying.
4. Resolve connected roles before exporting. Check generated files and compare both parts in game.

##### Small example
A sleeve and cuff share one original game diffuse but need blue and white replacement images. Assign both materials independently. Each draw receives its own replacement without changing the other part's original identity.

##### Check and recover
CrossIB, WWMI Cross-Scene, asset-name matching, custom/live templates and Partial Export are currently excluded. Disabling Auto Split hides and bypasses the material layer; it does not silently infer it anyway. Uninitialized materials retain their prior route—export does not initialize them on your behalf.

<a id="material-batching"></a>
#### Group draws by texture without losing required order

Reduce repeated texture setup by placing compatible parts with the same complete bindings together before final index ranges are built.

**Location:** Export compatibility → Group Draws by Texture; Material Tools → Preserve Draw Order.

##### Steps
1. Enable independent material textures. Group Draws by Texture defaults on.
2. Mark order-dependent game materials with Preserve Draw Order.
3. Export and inspect the intended transparency/layering.
4. Turn grouping off to compare against prior object ordering without disabling texture assignment or synchronization.

##### Small example
Opaque parts use bindings A/B/A/B. Grouping can produce A/A/B/B and share adjacent texture setup. A protected transparent trim between them remains a barrier, so grouping must not move other parts across it.

##### Check and recover
Grouping stays inside a Component and keeps per-object draw commands and visibility. Uninitialized, incompatible and recorded transparent/CPU-posed paths retain protection. Blender preview Alpha links or BLENDED settings alone are not game blend-state evidence, so they do not automatically disable batching. Use the explicit order option when you know the game needs it.

<a id="material-files"></a>
#### Understand filenames and why an old output texture stays unchanged

Replacement images keep their recorded basename and extension in Textures. Export adds missing files; it never overwrites an existing same-name output image.

**Location:** Mod output → Textures; generated mod.ini resource filename references.

##### Steps
1. Save each replacement to a clear file name before export.
2. Avoid two different source images with the same Windows-equivalent destination basename.
3. After export, follow the actual filename entry in mod.ini.
4. To update a previously delivered texture, deliberately replace that output file yourself, or save the source under a new name and export again.

##### Small example
Textures already contains **Sleeve_D.dds**, painted by you after the last export. Export preserves it even if the source Sleeve_D.dds differs in bytes or size. Saving your next source as **Sleeve_D_v2.dds** creates a new destination and updates the generated reference.

##### Check and recover
No output content comparison, automatic format conversion or old-file cleanup is implied. Conflicting incoming source files targeting one name still stop export. Resource section labels clean the file stem and may add a collision suffix, but that does not rename the delivered file. Dirty/unsupported image states must be saved first. A correct Blender preview is not final in-game validation.

<a id="game"></a>
## Game

Choose Arknights: Endfield for EFMI or Wuthering Waves for WWMI. The ordinary Extract → Import → Export modes come from the embedded game tools. Velo's compatibility controls and extra panels extend that workflow; they are not interchangeable shortcuts.

<!-- directory:start -->
- [Install, update and begin a project](#game-start)
- [Keep the five working stages separate](#game-files)
- [Extract an Endfield object source](#game-efmi-extract)
- [Filter extraction without removing the parts you need](#game-efmi-filters)
- [Import Endfield Components](#game-efmi-import)
- [Generate an Endfield named skeleton](#game-efmi-names)
- [Export an Endfield Mod with the right skeleton mode](#game-efmi-export)
- [Add Endfield distance LOD data](#game-efmi-lod)
- [Draw source geometry through another Component with CrossIB](#game-efmi-crossib)
- [Export Endfield custom ShapeKeys](#game-efmi-shapes)
- [Extract a WWMI character and its textures](#game-wwmi-extract)
- [Import WWMI geometry and source previews](#game-wwmi-import)
- [Choose Merged, Per-Component or from-Merged export](#game-wwmi-skeleton)
- [Export a complete WWMI Mod](#game-wwmi-export)
- [Extract WWMI LOD data](#game-wwmi-lod)
- [Merge several scene IB routes into one authoring source](#game-wwmi-crossscene)
- [Add another texture form without duplicating geometry](#game-wwmi-forms)
- [Use optional form anchors only as extra evidence](#game-wwmi-anchors)
- [Export WWMI custom ShapeKeys without replacing native ones](#game-wwmi-shapes)
- [Extract, edit and export Raw Mesh geometry](#game-wwmi-raw)
- [Choose Hash, Slot or captured asset-name matching](#game-texture-strategy)
- [Enable Slot textures and choose Components](#game-slot)
- [Control what exports: names, collections and material splitting](#game-scope)
- [Understand the original advanced options](#game-advanced)
- [Add the Mod's name, author, link and logo](#game-mod-info)
- [Build a simple object-visibility toggle](#game-toggles)
- [Use custom INI templates only when maintaining their full contract](#game-templates)
- [Use Partial Export only for a controlled buffer update](#game-partial)
- [Check output files and preserve author-managed textures](#game-output)
- [Troubleshoot by the stage that failed](#game-troubleshoot)
- [Read the few technical words that matter](#game-glossary)
<!-- directory:end -->

### Start here

<a id="game-start"></a>
#### Install, update and begin a project

Velo is the Blender-side tool. You also need the matching game-side loader and a valid capture; installing the add-on does not install or configure those.

**Location:** Blender Preferences → Add-ons; 3D Viewport → N → Velo Tools.

##### Steps
1. Download **velo_tools-1.7.1.zip** from the release assets, not GitHub's automatic source archive.
2. In Preferences → Add-ons, choose Install from Disk, select the zip and enable Velo-Tools.
3. Open the viewport sidebar with N. In Game choose the intended game.
4. Follow that game's extraction, import and export tutorials. Keep the first test small.

##### Small example
For your first Endfield project, import an extracted original, make one obvious but reversible mesh adjustment, and export a test copy before replacing the full outfit.

##### Check and recover
The declared minimum is Blender 3.6; 4.4 is the primary tested environment. Optional Robust dependencies have a narrower compatibility range. Update using Velo's host updater in Preferences and restart; do not separately update the embedded EFMI/WWMI cores. Standalone tools can coexist. Never run the updater on a development source junction, where replacement would affect the source repository.

<a id="game-files"></a>
#### Keep the five working stages separate

Most confusing import/export errors start with the wrong folder. A source folder describes the original game's data; an output folder contains your new Mod.

**Location:** Extract/Import/Export folder fields.

##### Steps
1. Preserve the fresh **Frame Dump**: captured buffers, textures, draw information and normally log.txt.
2. Extract an **object source**: Component files, Metadata and texture evidence.
3. Only for WWMI cross-scene work, generate a separate **merged source**.
4. Import into a Blender **Component collection** and save your blend.
5. Export to a separate **Mod output**. Never use that output as its own object source.

##### Small example
Use sibling folders called **Capture**, **Source**, **MergedSource** when needed, and **ModOutput**. Ordinary work skips MergedSource. Cross-scene work imports and exports from its merged root instead of an old child source.

##### Check and recover
Metadata.json describes geometry and bones; TextureUsage.json describes basic image use; ShaderTextureUsage.json adds captured shader/slot evidence. Bone-name sidecars, CrossIB.json and CrossSceneManifest.json belong with their producers' source. Regenerate stale evidence rather than inventing JSON fields or deriving a game Hash from image pixels.

### Arknights: Endfield · original EFMI workflow

<a id="game-efmi-extract"></a>
#### Extract an Endfield object source

Turn a capture into editable Component files and the evidence later export needs. Extracting a character is not the same as opening an arbitrary texture folder.

**Location:** Game → Arknights: Endfield → Mode: Extract Frame Data.

##### Steps
1. Set Frame Dump Directory to a fresh capture containing the intended object.
2. Choose an output directory; leaving it empty uses the dump location.
3. Review [filters](#game-efmi-filters). Keep Dirty Slot filtering on for later Slot work.
4. Enable Generate CrossIB.json if planning CrossIB. Enable Import after extraction for immediate inspection.
5. Extract and inspect the report and generated character folders.

##### Small example
Capture a character at the intended main-detail view, extract it, then find the folder containing its Component files and Metadata.json. That character folder—not the parent containing several characters—is the import source.

##### Check and recover
Tolerate extraction errors can skip failed objects; a partially successful run is not a complete source. Velo stores compact authoring group IDs separately from local-to-runtime bone mappings. Keep them together and re-extract old sources rather than borrowing another object's maps.

<a id="game-efmi-filters"></a>
#### Filter extraction without removing the parts you need

Filters narrow captured content. They do not determine whether a part is visually important, and some filters change the final Component numbering.

**Location:** EFMI Extract Frame Data → object/component/texture filters and Velo Compatibility Options.

##### Steps
1. Start from a fresh output folder so an older extraction cannot be mistaken for the new result.
2. Use object filters for static objects, minimum Component/texture counts or a specifically targeted resource Hash.
3. Use Component Keep/Skip ranges only after understanding the numbering. Skip wins over Keep.
4. Inspect actual retained output and its new continuous Component numbers.

| Option | Effect to check |
| --- | --- |
| Automatically skip LOD components | Opt-in; removes draws with no raw PS texture bindings before final numbering |
| Component Keep / Skip | Accepts entries such as 0,1,5-7; operates after automatic LOD filtering |
| Resource Hash targeting | An enabled nonempty explicit target bypasses the automatic LOD filter |
| Skip small textures / JPG | Can remove useful images; do not enable merely to reduce clutter |
| Skip Dirty Slots | Removes stale inherited bindings only when usable capture-log evidence exists |

##### Small example
Keep **0-8**, Skip **4,6** retains post-LOD indices 0-3,5,7-8, then renumbers the result. A replacement named for the old Component 7 may need review against that new source.

##### Check and recover
Without usable log evidence, Dirty Slot filtering preserves legacy output rather than guessing deletions. A missing original should be investigated at extraction, not “fixed” by assigning its replacement to an unrelated image.

<a id="game-efmi-import"></a>
#### Import Endfield Components

Import the extracted model and choose whether group identities are shared across Components or remain local to each part.

**Location:** EFMI → Mode: Import Object.

##### Steps
1. Set Object Source Directory to the character folder from extraction.
2. Choose vertex-color storage and Merged or Per-Component import.
3. Enable component sub-collections if desired; these create C0, C1 and so on.
4. Keep Import Textures on for source previews. Keep Mirror Mesh consistent with the intended orientation.
5. Import, then inspect geometry, groups, materials and collection structure.

##### Small example
Choose Merged to edit a whole outfit with shared authoring group identities. Choose Per-Component when deliberately working with each part's local numbering.

##### Check and recover
Merged requires the appropriate Metadata v4 mapping. For named import, first generate [named-bone sidecars](#game-efmi-names), then enable Import bone-name mapping and skeleton in compatibility options. That option is off by default. Importing into child collections requires nested collection export to remain enabled.

<a id="game-efmi-names"></a>
#### Generate an Endfield named skeleton

Bring original bone names and an oriented hierarchy into a Merged EFMI project using matching unpacked LOD0 assets.

**Location:** Game → Endfield → Named Bone Mapping; Import Object compatibility options.

##### Steps
1. Set the matching EFMI object source.
2. Select a LOD0 GLB, or a supported unpacked character root containing one Avatar and raw Unity YAML LOD0 Mesh/prefab assets.
3. Generate Bone Name Mapping. Keep **BoneNameMapping.json** and **BoneNameSkeleton.glb** with the source.
4. Select Merged import and enable Import bone-name mapping and skeleton. Optionally enable .L/.R pair renaming.

##### Small example
Your numeric imported sleeve is difficult to pose. Generate the mapping from that same character's original LOD0 assets, then import a new test collection with names and an Armature modifier.

##### Check and recover
1.7.1 proves identities using complete corresponding skin weights; old v1 sidecars must be regenerated as v2. No parent/sibling-folder fallback GLB is guessed. Ambiguous or missing evidence is a failure to resolve, not an invitation to copy another Component's local bone table.

<a id="game-efmi-export"></a>
#### Export an Endfield Mod with the right skeleton mode

The three export choices have different runtime numbering. Select according to how the project was imported and what runtime path is intended.

**Location:** EFMI → Mode: Export Mod.

##### Steps
1. Set Component Set, matching Object Source Directory and a separate Mod Output Directory.
2. Choose the mode below. Keep full INI generation and texture copying for the first test.
3. Review [scope and material splitting](#game-scope); leave unrelated advanced features off.
4. Export, inspect the report and test the Mod in game.

| Mode | Meaning |
| --- | --- |
| Merged (Unified Vertex Groups) | Shared authoring IDs are translated back to each Component's local runtime |
| Per-Component | The project already uses each Component's local groups |
| Merged (Merged Skeleton) | Shared authoring IDs/names resolve through the source's finalized runtime map into the merged skeleton |

##### Small example
A project imported as Merged normally defaults its next export to Merged Skeleton. Choose the other Merged option explicitly only when you intend shared authoring with the older per-Component runtime.

##### Check and recover
Unresolved weighted identities stop export; changing an object's Component to borrow unrelated mapping is not a fix. Version 1.7.1 automatically adds missing shader-resource/unordered-access capabilities to the exact merged-skeleton resource, avoiding folder-name-dependent visibility. Complete explicit upstream flags are left unchanged; exports do not check the internet. Test actual runtime behavior separately.

<a id="game-efmi-lod"></a>
#### Add Endfield distance LOD data

LOD is the alternate geometry the game uses at distance. A close-up capture alone cannot describe every distant version.

**Location:** EFMI → Mode: Extract LOD Data.

##### Steps
1. Extract the main object first.
2. Capture while the desired distance geometry is actually drawn.
3. Set LOD Frame Dump and the existing object source.
4. Extract with default matching first; adjust filters or matching only after reading failures.
5. Export and test near/far transitions.

##### Small example
The edited coat works in a close-up but returns to the original at distance. Add the actually drawn distant LOD evidence to the source, then re-export and revisit both distances.

##### Check and recover
Overwrite is opt-in: replacing a colliding LOD dataset must not preserve stale fragments. Unmatched Components can retain a full-detail fallback marked absent; this is not proof a real LOD was captured. Allow Export Without LODs bypasses a requirement, not the game's distance behavior.

### Arknights: Endfield · Velo extensions

<a id="game-efmi-crossib"></a>
#### Draw source geometry through another Component with CrossIB

CrossIB lends selected source geometry to another Component's rendering pass. It is not WWMI multi-scene merging, and the mapping arrow has a specific direction.

**Location:** EFMI Export Mod → Cross Index Buffer.

##### Steps
1. Use full export and enable CrossIB.
2. Ensure the source contains CrossIB.json v2; Generate/Regenerate asks for a current relevant Frame Dump.
3. Add an object mapping for one mesh, or a collection mapping for a planned group.
4. Set the right-hand target Component, then export.
5. Inspect the borrowed pass and ordinary parts in game.

##### Small example
You have a trim mesh that must be drawn through a compatible other pass. The trim is the source on the left; the consuming Component is the target on the right. Reversing them asks for a different result.

##### Check and recover
Regeneration replaces one evidence file; it does not merge unrelated scene dumps. A dump without the target is rejected without replacing valid evidence. Export supplies CrossIBClassifier.ini with the 200-205 capability ABI when mappings exist. Do not mix this route with the currently unsupported independent Material Tools export layer.

<a id="game-efmi-shapes"></a>
#### Export Endfield custom ShapeKeys

Expose deliberately numbered keys as runtime controls. Ordinary unnumbered keys remain authored base-shape adjustments at their current values.

**Location:** EFMI Export Mod → Advanced → Export Custom ShapeKeys.

##### Steps
1. Put a key such as **Deform 12 CapeLift** on a mesh in the export collection.
2. Enable Export Custom ShapeKeys. Keep Merge Buffer Files on for normal use.
3. Inspect the live detected list; click entries to locate objects.
4. Resolve duplicate IDs on one object and conflicting values across contributors, then fully export.

##### Small example
Set Deform 12 CapeLift to 0.375. Export creates a persistent **$ShapeKey_12** initialized to that value, while the base position buffer remains neutral for that runtime key. An unnumbered Fit key at 0.2 is baked into the exported base instead.

##### Check and recover
Whitespace/case variations such as Deform2Blink are accepted, but the number is the identity. One ID shares a control across contributing parts. With merged buffers, each Component gets two extra merged files independent of key count; disabling it uses legacy per-slot files. The UI summary alone does not enable this runtime path.

### Wuthering Waves · original WWMI workflow

<a id="game-wwmi-extract"></a>
#### Extract a WWMI character and its textures

Extract the character while the desired geometry and texture form are actually visible. A capture of one form or distance is not evidence for every other one.

**Location:** Game → Wuthering Waves → Mode: Extract Frame Data.

##### Steps
1. Set a fresh Frame Dump and output parent folder.
2. Review small-texture, JPG, known-cubemap and same-slot/same-Hash filters.
3. Keep Skip Dirty Slots for evidence-based removal of stale inherited bindings.
4. Extract and inspect the target object folder, Metadata, textures and ShaderTextureUsage.json.

##### Small example
Extract a near-distance base form first. If an alternate form changes only its textures, later use [Form Texture Merge](#game-wwmi-forms) rather than editing the base JSON by hand.

##### Check and recover
A missing useful image may have been filtered. Dirty Slot handling retains only proven special inherited service uses when valid evidence exists; it is not “keep every bound slot.” Without usable log evidence, legacy output is preserved. Missing facial ShapeKey data is usually a reason to capture during facial animation, not to enable the advanced missing-data bypass without review.

<a id="game-wwmi-import"></a>
#### Import WWMI geometry and source previews

Import from one extracted character folder, or from the final merged root for cross-scene work. Keep the selected skeleton convention consistent through authoring.

**Location:** WWMI → Mode: Import Object.

##### Steps
1. Select the correct Object Source Directory.
2. Choose vertex-color storage and Merged or Per-Component import.
3. Enable component sub-collections and texture import as needed.
4. Keep Mirror Mesh consistent with your intended left/right orientation.
5. Import and inspect the component tree and vertex-group names.

##### Small example
Import a base outfit as Merged into C0/C1/etc. collections, then use [original bone-name mapping](#vg-wwmi-map) if you have matching unpacked assets.

##### Check and recover
Source texture preview prefers ShaderTextureUsage.json and falls back to TextureUsage.json. An absent preview is not automatically missing geometry. Skip Empty Vertex Groups changes the imported list; do not confuse list cleanup with conversion between local and shared bone identities.

<a id="game-wwmi-skeleton"></a>
#### Choose Merged, Per-Component or from-Merged export

Import offers two authoring layouts; export offers a third path that translates shared authoring groups into local runtime groups.

**Location:** WWMI Import/Export → skeleton mode.

##### Steps
1. Decide whether editing needs shared groups across Components.
2. Import using that authoring convention.
3. Choose an export strategy from the table.
4. Test animation and the relevant multiple-character conditions in game.

| Strategy | Use and tradeoff |
| --- | --- |
| Merged | Shared authoring, cross-Component weights and skeleton scale; native runtime has a one-frame update delay and pauses when identical targets coexist |
| Per-Component | Local authoring/runtime, no one-frame delay; restricted bone scope and no custom skeleton scale |
| Per-Component (from Merged) | Shared authoring translated to local runtime; strict per-Component bone membership |

##### Small example
You want easy shared-group editing but local runtime behavior. Import Merged and export Per-Component (from Merged). If a sleeve uses a bone outside its allowed Component map, fix that influence rather than bypassing the error.

##### Check and recover
Do not choose from-Merged for data already authored with local IDs. A numeric name alone does not tell you which numbering system it belongs to. The original source Metadata and selected mode must agree.

<a id="game-wwmi-export"></a>
#### Export a complete WWMI Mod

The normal export builds a complete mod.ini and required data from the configured collection and source. Partial Export is not the first-export path.

**Location:** WWMI → Mode: Export Mod.

##### Steps
1. Set Component collection, the exact object source used for the project, and a separate output.
2. Choose the [skeleton strategy](#game-wwmi-skeleton).
3. Keep Copy Textures, Write mod.ini and useful comments enabled.
4. Check collection visibility and Auto Split by Material.
5. Export and inspect the final report, Meshes, Textures and mod.ini.

##### Small example
After changing one sleeve, fully export into a test Mod folder. Check that both unchanged body geometry and the edited sleeve load before considering faster buffer-only updates.

##### Check and recover
Default texture replacement is Hash-style; optional Slot is a separate decision. Auto Split obeys the same whole-pipeline enabled/disabled rule in ordinary, from-Merged and Cross-Scene exports. Source objects are prepared on temporary copies; an export does not require permanently renaming every authored object.

### Wuthering Waves · Velo extensions

<a id="game-wwmi-lod"></a>
#### Extract WWMI LOD data

Add evidence for alternate distance geometry to an existing source. LOD extraction is a separate panel, not another ordinary base extraction into the same directory.

**Location:** Game → Wuthering Waves → LOD Data Extraction.

##### Steps
1. Extract the main object first, then capture the desired LOD while it is visible.
2. Set the LOD dump and existing WWMI object source.
3. Keep default matching initially; use Hash/minimum-vertex filters only when needed.
4. Extract, then fully export and test distance transitions.

##### Small example
The hair replacement works nearby but not at medium distance. Capture the medium-distance model, add its LOD data and test walking toward/away from it repeatedly. Check that the corrected distant result does not break the original close view.

##### Check and recover
Existing LODs are protected unless overwrite is enabled. Advanced matching includes voxel/point-cloud method, error threshold, sample/voxel size and candidate counts; alter the parameter related to the reported mismatch. After changing the source skeleton or cross-scene source, regenerate affected LOD data rather than transplanting old remaps.

<a id="game-wwmi-crossscene"></a>
#### Merge several scene IB routes into one authoring source

Use Cross-Scene Merge when the same target is rendered through different IB routes in different scenes. An IB describes an index/draw route, not a texture file.

**Location:** Game → Wuthering Waves → Cross-Scene Merge.

##### Steps
1. Prepare a base extraction and one extraction for each additional scene.
2. Set Base, add each source row, and choose a role below.
3. Set a new Output folder and run Merge across scenes.
4. Import that merged root with normal WWMI import and keep it as Object Source Directory for export.
5. Test every included scene after a cold entry and after switching scenes.

| Role | What becomes editable |
| --- | --- |
| Fold into Base | Base geometry represents this route; incompatible buffers can retain an own-buffer path |
| Editable | This route keeps independent geometry and a separate identity |
| Merge Form | Same-vb0, structurally identical extraction contributes texture-form evidence to one Fold row, not duplicate geometry |

##### Small example
Use a showcase extraction as Base and fold a compatible world route. A genuinely different form with separate geometry belongs in Editable, not an arbitrary texture-only merge.

##### Check and recover
The self-contained root contains Metadata, STU, Component files and CrossSceneManifest.json v3; it does not need scene_ibs child folders. Old routing-v2 sources must be re-merged. _ibN suffixes identify ownership, not a second local Component number. Custom/live templates and the independent Material Tools layer are excluded.

<a id="game-wwmi-forms"></a>
#### Add another texture form without duplicating geometry

Merge texture evidence from a raw extra-form dump into an existing source. This is for the same geometry identity, not a substitute for an independently editable form.

**Location:** Game → Wuthering Waves → Form Texture Merge.

##### Steps
1. Capture a near-distance extra form after its textures have loaded.
2. Set Form Dump, current Object Source Directory and a stable form label.
3. Run Merge Form Texture Data.
4. Repeat for additional forms or distances. Reuse the same label to accumulate evidence for that same form; use base for the base form.

##### Small example
The same outfit changes texture when entering a powered state. Merge a powered-form capture under **powered**. A later farther capture of that state uses the same label, not a new fake form.

##### Check and recover
The operation updates STU and retains required form images. On a current cross-scene root it updates root evidence through the manifest's Component mapping, not child STU files. Different-vb0 or structurally different geometry belongs in its own domain. Test repeated switching and streamed textures, not only one still image.

<a id="game-wwmi-anchors"></a>
#### Use optional form anchors only as extra evidence

An anchor is a captured signal that a form appeared. It can narrow an already safe texture branch; it cannot distinguish forms whose local Slot evidence is fundamentally ambiguous.

**Location:** WWMI Slot compatibility → formid Auxiliary Criterion and anchor finder.

##### Steps
1. Leave the option off for the first safe Slot export.
2. If needed, set the base dump and add labeled extra-form dump rows in the finder.
3. Find candidates, review them and Apply the intended anchor; reset candidates after replacing captures.
4. Test every form, including a form with no visible anchor.

##### Small example
A verified form-specific vb0 can be written as **1234abcd:powered** in the accepted Hash:label syntax. This is illustrative syntax, not a usable identity from your character.

##### Check and recover
Supported manual identities are 8-character vb0 hashes and 16-character pixel-shader hashes. IB and vertex-shader hashes are not valid substitutes. If exactly one form lacks anchors, elimination can identify it when no anchored form appears. Refresh after game changes; do not use unrelated UI/VFX evidence without confirming its association.

<a id="game-wwmi-shapes"></a>
#### Export WWMI custom ShapeKeys without replacing native ones

The source Metadata determines which Deform IDs are native. Custom position changes are composed separately, so the game's native deformation path stays intact.

**Location:** WWMI Export Mod → Export Custom ShapeKeys; Mesh Tools for naming.

##### Steps
1. Load the correct object source and use [safe automatic numbering](#mesh-shape-number).
2. Keep Export Custom ShapeKeys enabled for custom controls; it defaults on in ordinary and Cross-Scene export.
3. Set consistent values for contributors sharing one numeric ID.
4. Fully export and test native expressions plus your custom control.

##### Small example
Create **CapeLift** and let the numbering tool find an ID outside that source's reserved ranges. Its output variable is **$ShapeKey_<chosen ID>**, initialized from Blender. Do not assume 1 is available merely because no such key is currently visible.

##### Check and recover
Each native batch reserves a 127-ID range. Disabling custom export removes external controls without pushing them into native buffers. Zero effective custom delta emits no empty resources. Unnumbered keys bake current values into Basis. Persisted d3dx_user.ini values can override new INI defaults; conflicting authored values stop export. Partial Export cannot update this whole pipeline.

<a id="game-wwmi-raw"></a>
#### Extract, edit and export Raw Mesh geometry

Raw Mesh is for suitable VFX, scenery or static geometry outside the ordinary skinned-character detection chain. It is not a generic shortcut around character extraction errors.

**Location:** Game → Wuthering Waves → Raw Mesh Tools.

##### Steps
1. In Raw Mesh Extract mode, set the dump, output folder and comma/newline-separated VB/IB hashes.
2. Leave Position Element empty unless automatic detection needs an explicit correction.
3. Extract, then switch Raw Mesh to Import and select its generated folder.
4. Edit a copy. In Export select the collection, output and Auto/Faithful/Rebuild.
5. Verify the intended geometry and other captured effects in game.

| Mode | What you may change |
| --- | --- |
| Auto | Chooses faithful passthrough if topology is unchanged, otherwise rebuild |
| Faithful | Re-encodes position, preserves other stored bytes; topology must stay unchanged |
| Rebuild | Permits topology changes; nonstandard data is best-effort and can be lossy |

##### Small example
Move existing points of a captured static ribbon without changing its triangles: Faithful is the controlled experiment. Adding subdivisions requires Rebuild and a new review of attributes that Blender may not represent.

##### Check and recover
A VB hash selects its whole object and draws; an IB hash selects the matching draw/component. Ambiguous hashes are rejected. Faithful refuses incompatible topology rather than inventing correspondence. Output is independent plain 3Dmigoto overrides, not the standard character skeleton runtime.

### Texture strategies and common export controls

<a id="game-texture-strategy"></a>
#### Choose Hash, Slot or captured asset-name matching

These are ways to recognize where a texture replacement belongs. They are not three image formats.

**Location:** Game → Export Mod → Velo Compatibility Options.

##### Steps
1. Start with native Hash replacement when the captured identities remain suitable.
2. Consider [Slot-style](#game-slot) when you have fresh binding evidence for streaming or identity changes.
3. For WWMI asset-name matching, capture with TextureAssetManifest.jsonl, re-extract, and enable Use Asset-Name Matching.
4. Test all affected forms/distances, not just initial loading.

| Mode | Evidence it needs |
| --- | --- |
| Hash | The captured texture resource identity |
| Slot | Enough Component/pass binding and format evidence to choose safe runtime branches |
| Asset name, WWMI | Captured full asset-path evidence on retained exported texture records |

##### Small example
A texture with the same appearance has a different runtime Hash after streaming. Repainting its pixels does not recover the missing identity. Capture that state and use a strategy supported by the new evidence.

##### Check and recover
Asset-name and Slot modes are mutually exclusive. Asset-name output uses the captured short name; known duplicate short names with different full paths are rejected. Records without captured path evidence stay on native Hash. Never invent asset paths from filenames.

<a id="game-slot"></a>
#### Enable Slot textures and choose Components

Bind replacements during the correct Component draw, then restore the previous texture state. The exporter determines slots from capture evidence, not from the replacement image's color.

**Location:** EFMI/WWMI Export Mod → Slot-style Textures → Slot Export Mode and Component list.

##### Steps
1. Re-extract a current source with usable ShaderTextureUsage.json and format evidence.
2. Enable Slot-style Textures. Choose Native Format Read by default; its ps-tN->Format syntax requires XXMI Libs 1.1.0 or newer. Fuzzy Format Matching is the deliberate legacy alternative.
3. Click List components and uncheck those that should retain Hash. An untouched empty list means all eligible Components.
4. Export with a supported template and test enabled and opted-out parts.

##### Small example
The body needs Slot replacement, but a particular accessory should remain Hash-driven. Populate the list and uncheck the accessory instead of disabling Slot for the whole Mod.

##### Check and recover
Refresh preserves existing choices and selects new Components. Shared hashes may still need a native Hash override for opted-out owners. Missing formats, ambiguous branches or missing required draw-range evidence stop export; do not broaden predicates until it “works.” Empty ResourceBypassPST handles are runtime backup references, not junk to delete.

<a id="game-scope"></a>
#### Control what exports: names, collections and material splitting

The export Component collection is the root of the operation. Visibility/nesting settings decide which objects qualify; material splitting decides how eligible geometry is assigned.

**Location:** Export Mod → Component collection, ignore options and Auto Split by Material.

##### Steps
1. Put the intended meshes under the configured root.
2. Review Ignore Nested Collections, Ignore Hidden Collections and Ignore Hidden Objects.
3. With Auto Split on, inspect material prefixes and planned split destinations.
4. With Auto Split off, give every whole export object its correct Component name.
5. Export and verify all intended objects—not merely a nonempty output folder.

##### Small example
C0/C1 are child collections. Ignoring nested collections can leave nothing to export. A hidden ancestor can also exclude an otherwise visible child. After separating a sleeve and moving it in the Outliner, its real collection ownership should remain authoritative.

##### Check and recover
Split on uses temporary material-aware preparation; split off bypasses material routing throughout, including independent material overrides. It does not disable MMD mapping, ShapeKeys, modifier application or skeleton validation. Multiple collection links use eligible visible in-scope paths, not an arbitrary hidden link.

<a id="game-advanced"></a>
#### Understand the original advanced options

Advanced switches change generated data or runtime behavior. They are not a checklist that should all be enabled for better quality.

**Location:** EFMI/WWMI Export Mod → main controls and Advanced.

##### Steps
1. Keep a working baseline export and change one relevant setting.
2. Use the table to identify what the control actually changes.
3. Fully export when INI/runtime behavior changes.
4. Compare geometry, animation and visibility in the conditions affected.

| Control | What it means |
| --- | --- |
| Mirror Mesh | Mirrors data for in-game orientation; it is not simply object Scale X |
| Apply all modifiers | Evaluates the intended modifier result during export; check topology and ShapeKey interactions |
| Ignore muted ShapeKeys | Excludes muted keys from the relevant evaluated/export path |
| Add Missing Vertex Groups | Fills numeric placeholders; does not invent weights |
| Fill Missing Mesh Data | Adds game-specific default layers; WWMI and EFMI defaults differ |
| Skeleton Scale | Runtime model scaling where the selected skeleton mode supports it |
| EFMI Max Instance Count | Capacity for merged-skeleton instances, with upfront memory cost |
| EFMI Spatial Identification | Uses position-related ownership evidence for weighted objects; threshold must fit the available unique Components |

##### Small example
To test runtime scale, keep the supported Merged mode and compare a small change from 1.0. Do not enable unrelated missing-data or identity switches at the same time.

##### Check and recover
WWMI's legacy Unrestricted Custom Shape Keys is not Velo's independent custom ShapeKey switch. Missing-data bypasses do not synthesize trustworthy capture evidence. Debug options such as retaining temporary objects or Export On Reload belong to diagnosis, not routine distribution.

<a id="game-mod-info"></a>
#### Add the Mod's name, author, link and logo

Describe the delivered Mod using the original Mod Info panel. This metadata does not determine which geometry is exported.

**Location:** EFMI/WWMI Export Mod → Mod Info.

##### Steps
1. Fill Mod Name, Author, Description and Link.
2. If using a logo, prepare a **512×512 DDS in BC7 SRGB**.
3. Select it as Mod Logo and perform a full export.
4. Check Textures/Logo.dds and the generated metadata references.

##### Small example
Set a clear outfit title and a link to its release instructions. Add a correctly encoded logo rather than renaming a PNG to .dds.

##### Check and recover
Changing the extension does not convert the image format. WWMI Cross-Scene keeps one shared Mod Info payload while each IB retains its own registration. Partial Export intentionally does not deliver complete metadata/assets.

<a id="game-toggles"></a>
#### Build a simple object-visibility toggle

An INI toggle changes a state variable and makes linked objects visible in selected states. It is not an interactive GUI builder.

**Location:** EFMI/WWMI Export Mod → INI Toggles.

##### Steps
1. Enable INI toggles and click Add Var. Give it a clear name and configure a non-conflicting hotkey in Edit Var.
2. Add states and add the intended objects to the states where they should be visible.
3. Check the default state; temporarily show empty states/default conditions while learning.
4. Fully export and cycle every state in game.

##### Small example
Create **coat_style** with one state showing CoatLong and another showing CoatShort. The body stays outside that choice. Verify the key cycles between the two coats without removing the body.

##### Check and recover
Spaces combine keys; semicolons separate alternative combinations. Advanced conditions evaluate AND before OR, so inspect grouping. Expand/collapse only affects editing visibility. Import/export uses JSON text in the editor; review Replace/Clear variables before importing. Back up a working configuration before deleting variables, states or object entries.

<a id="game-templates"></a>
#### Use custom INI templates only when maintaining their full contract

A custom Jinja2 template can replace the generated INI structure. It does not merely append one harmless line to the default output.

**Location:** ordinary single-source Export Mod → INI Template.

##### Steps
1. Keep the default template for normal workflows.
2. For deliberate template development, save a copy of the existing output and choose a Blender text block or external template file.
3. Open the template editor and make a controlled change.
4. Start live updates only against an intended test output. Stop updates before switching projects or exporting another Mod.

##### Small example
An experienced author adds a presentation comment in a private test template and compares the full generated INI with the baseline before attempting runtime changes.

##### Check and recover
A stale template can omit newer required resources. WWMI Cross-Scene rejects custom/live templates because its direct compiler owns the entire multi-IB contract. Slot transformations and independent Material Tools export also have template restrictions. Reset to the default and fully export to return to the maintained path.

<a id="game-partial"></a>
#### Use Partial Export only for a controlled buffer update

Write selected buffer classes into an already complete Mod. Partial Export skips INI generation and asset copying, so its output alone is not distributable.

**Location:** WWMI Export Mod → Advanced → Partial Export → buffer selection.

##### Steps
1. First produce and retain a complete known-working export.
2. Confirm exactly what changed and that the existing INI and all unchanged resources still match.
3. Enable Partial Export and select the required classes: indices, positions, blends, vectors, colors, UVs or supported shape data.
4. Test the update. Return to full export before publishing or after structural/feature changes.

##### Small example
An advanced author changes only compatible position data in an existing Mod and deliberately updates the position buffer. If the edit also changes topology or draw ranges, a positions-only update is not sufficient.

##### Check and recover
Do not use partial export for new material routing, custom ShapeKey pipeline changes, or a new standalone package. No new INI is expected from this mode. Preserve the previous complete folder so a mismatched buffer set can be restored.

### Output, troubleshooting and reference

<a id="game-output"></a>
#### Check output files and preserve author-managed textures

A complete export normally contains mod.ini, Meshes and Textures, plus optional support shaders. A success message should be followed by file and runtime checks.

**Location:** Mod Output Directory and the game's Mod loader.

##### Steps
1. Read the final report. Treat an audit error as a failed export.
2. Confirm every filename referenced by INI exists.
3. Check that the intended texture files—not an older same-name output—are being loaded.
4. Test cold load, reload, character switch, each scene/form, and required near/far states.

##### Small example
Your new source texture is not visible because the output already has an author-edited file of the same name. Preserve or deliberately update it according to [file delivery rules](#material-files), then test again.

##### Check and recover
Standard retained Component DDS/JPG textures are managed by extraction/export; custom auxiliary images such as a hand-authored mask are the author's responsibility. In a cross-scene root, standard canonical filenames must agree with STU ownership. Removing an original from that live root excludes its new generated route; an old output file may remain unused. Static checks do not prove the game displayed the replacement.

<a id="game-troubleshoot"></a>
#### Troubleshoot by the stage that failed

Identify whether the failure belongs to capture, source generation, Blender editing, export or runtime. Repair that stage instead of changing unrelated settings.

**Location:** Blender's operation report, the configured folders and the relevant tutorial.

##### Steps
1. Record the exact operation and the full message; save a copy of the project.
2. Use the symptom table to choose the next check.
3. Reproduce with the smallest relevant change and re-export into a controlled output.
4. Compare both the repaired behavior and something that previously worked.

| Symptom | First useful check |
| --- | --- |
| No game panels | Add-on enabled, correct top Tab/game, reload/restart after update |
| No Component objects | Export root, nesting/visibility and Component names |
| Missing/obsolete mapping | Correct source and current extraction or v2 bone-name regeneration |
| Texture unchanged | Actual output filename, retained same-name file, chosen original and export mode |
| Slot export blocked | Current STU, required formats and distinguishable branches |
| Mirror does nothing | Local-X direction, selected endpoints, unlocked reciprocal pairs |
| ShapeKey not controllable | Numbering, native reservations, export toggle, nonzero delta and persisted value |
| Different scene/distance fails | Capture that route/form/LOD; do not reuse unrelated evidence |

##### Small example
A source selector turns red after joining meshes. It is a missing exact name, not a weight-engine failure. Choose the actual merged name or recreate the intended same-named mesh, then retry.

##### Check and recover
Undo authoring operations where supported; use saved project/output copies for larger recovery. Never assume an output folder is untouched after a failed multi-step export—inspect it before distribution. No tool result replaces testing the actual game conditions you intend to support.

<a id="game-glossary"></a>
#### Read the few technical words that matter

Learn these words as practical file/operation concepts. You do not need to understand the entire rendering engine to follow the handbook.

**Location:** Reference for all five tabs.

##### Steps
1. When a tutorial uses an unfamiliar term, find it below.
2. Relate it to your current folder, object or action.
3. Return to that tutorial and change only the matching control.

| Term | Plain meaning |
| --- | --- |
| Component | An extracted part/draw identity; not simply any mesh object |
| VB / IB | Stored vertex data / triangle-index data |
| Hash | Captured resource identity, not a visible color or a guaranteed filename checksum |
| Slot / ps-tN | A texture binding position used during a shader draw |
| Draw / pass | One drawing operation / rendering stage that can use different resources |
| STU | ShaderTextureUsage.json: captured texture-binding evidence |
| Basis / ShapeKey | Base mesh shape / a stored change from a reference shape |
| Mapping | A dictionary between names or identities; not the weight transfer itself |
| LOD | Geometry used at a particular detail/distance level |
| Sidecar | Generated supporting file kept beside the main source |
| Fail closed | Stop when required evidence is uncertain instead of silently guessing |

##### Small example
“This Component has no safe Slot evidence” means the tool cannot prove where to bind a texture during that part's draw. It does not mean the PNG is necessarily corrupt.

##### Check and recover
Examples use sample names and numbers. Actual Component, bone, slot and Hash identities must come from your own project. For a new feature, look under the same top Tab and panel position as the add-on rather than searching a chronological release-note list.
