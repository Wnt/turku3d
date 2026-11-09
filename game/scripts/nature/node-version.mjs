// Geometry and raster output can vary between V8 versions. Keep both nature entry points on Node 24.
export function requireNatureNode(version = process.versions.node) {
  if (version.split('.')[0] !== '24') {
    throw new Error(`Nature asset builds require Node.js 24.x; found ${version}. Run "nvm install 24 && nvm use 24" from the repository root before building or baking impostors.`);
  }
}

requireNatureNode();
