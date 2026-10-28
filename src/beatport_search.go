package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"

	"unspok3n/beatportdl/internal/beatport"
)

const outputPrefix = "MUSICDL_JSON:"

func readSecret(path string) (string, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	value := strings.TrimRight(string(content), "\r\n")
	if value == "" {
		return "", fmt.Errorf("secret vide")
	}
	return value, nil
}

func run() error {
	usernameFile := flag.String("username-file", "/run/secrets/beatport_username", "fichier contenant l'identifiant")
	passwordFile := flag.String("password-file", "/run/secrets/beatport_password", "fichier contenant le mot de passe")
	cacheFile := flag.String("cache-file", "/state/beatportdl/beatportdl-credentials.json", "cache OAuth BeatportDL")
	proxy := flag.String("proxy", "", "proxy HTTP facultatif")
	collection := flag.String("collection", "", "playlist ou chart")
	authOnly := flag.Bool("auth-only", false, "valider le compte sans recherche")
	flag.Parse()

	query := strings.TrimSpace(strings.Join(flag.Args(), " "))
	if query == "" && !*authOnly {
		return fmt.Errorf("recherche vide")
	}
	username, err := readSecret(*usernameFile)
	if err != nil {
		return fmt.Errorf("identifiant Beatport indisponible: %w", err)
	}
	password, err := readSecret(*passwordFile)
	if err != nil {
		return fmt.Errorf("mot de passe Beatport indisponible: %w", err)
	}

	auth := beatport.NewAuth(username, password, *cacheFile)
	client := beatport.New(*proxy, auth)
	if err := auth.LoadCache(); err != nil {
		if err := auth.Init(client); err != nil {
			return fmt.Errorf("authentification Beatport: %w", err)
		}
	}
	if *authOnly {
		fmt.Printf("%s{\"authenticated\":true}\n", outputPrefix)
		return nil
	}
	var results interface{}
	if *collection != "" {
		id, parseErr := strconv.ParseInt(query, 10, 64)
		if parseErr != nil || id <= 0 {
			return fmt.Errorf("identifiant invalide")
		}
		var name string
		var total int
		tracks := []beatport.Track{}
		for page := 1; ; page++ {
			previousCount := len(tracks)
			var next *string
			if *collection == "playlist" {
				if page == 1 {
					meta, e := client.GetPlaylist(id)
					if e != nil {
						return e
					}
					name = meta.Name
					total = meta.TrackCount
				}
				items, e := client.GetPlaylistItems(id, page, "per_page=100")
				if e != nil {
					return e
				}
				for _, item := range items.Results {
					tracks = append(tracks, item.Track)
				}
				next = items.Next
			} else if *collection == "chart" {
				if page == 1 {
					meta, e := client.GetChart(id)
					if e != nil {
						return e
					}
					name = meta.Name
					total = meta.TrackCount
				}
				items, e := client.GetChartTracks(id, page, "per_page=100")
				if e != nil {
					return e
				}
				tracks = append(tracks, items.Results...)
				next = items.Next
			} else {
				return fmt.Errorf("collection inconnue")
			}
			if len(tracks) == previousCount && next != nil {
				return fmt.Errorf("pagination Beatport incomplete")
			}
			if next == nil {
				break
			}
		}
		if len(tracks) != total {
			return fmt.Errorf("lecture Beatport incomplete (%d/%d titres)", len(tracks), total)
		}
		results = map[string]interface{}{"name": name, "total": total, "tracks": tracks}
	} else {
		results, err = client.Search(query)
	}
	if err != nil {
		return fmt.Errorf("recherche Beatport: %w", err)
	}
	payload, err := json.Marshal(results)
	if err != nil {
		return fmt.Errorf("encodage des résultats: %w", err)
	}
	fmt.Printf("%s%s\n", outputPrefix, payload)
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "beatport-search:", err)
		os.Exit(1)
	}
}
