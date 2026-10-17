import { moduleManifests } from './modules';
import { installViewApiFirewall } from './viewApiScope';

// Installed first: every request made through the fetch pipeline (WP-9.2)
// passes the workspace scope and gets the client headers.
installViewApiFirewall(moduleManifests);
