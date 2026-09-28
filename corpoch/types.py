import pydantic, typing, pydantic.alias_generators
from datetime import datetime

CH_MODIFIERS = (
	("NM", "No Modifiers"),
	("AH", "All Hopos"),
	("AS", "All Strums"),
	("AT", "All Taps"),
	("AO", "All Opens"),
	("BM", "Brutal Mode"),
	("DD", "Deadly Dynamics"),
	("DK", "Double Kick"),
	("DN", "Double Notes"),
	("DS", "Dropless Sustains"),
	("NK", "No Kick"),
	("NS", "Note Shuffle"),
	("PM", "Precision Mode"),
)

CH_VERSIONS = (
	("v1.0.0.4080-final", "v1.0.0.4080-final"),
	("v1.1.0.6085-final", "v1.1.0.6142-final"),
)

CH_INSTRUMENTS = (
	("guitar", "Guitar"),
	("coop", "Guitar Coop"),
	("bass", "Bass"),
	("rhythm", "Rhythm"),
	("keys", "Keys"),
	("drums", "Drums"),
	("ghl", "GHL Guitar"),
	("ghlbass", "GHL Bass"),
	("ghlrythm", "GHL Rhythm"),
	("ghlkeys", "GHL Keys"),
	("ghlcoop", "GHL Guitar Coop"),
)

CH_DIFFICULTIES = (
	("expert", "Expert"),
	("hard", "Hard"),
	("medium", "Medium"),
	("easy", "Easy"),
)

CHART_CATEGORIES = (
	("none", "None"),
	("hybrid", "Hybrid"),
	("fret", "Fret"),
	("strum", "Strum"),
	("sprint", "Sprint"),
	("marathon", "Marathon"),
)

TB_RULESETS = (
	("single", "Single TB"),
	("csc", "CSC TB Rules"),
	("banpick", "'NPDO' Ban/Pick"),
	('refdecide', "Ref picks from unplayed"),
	('bansave', "Ban-Save"),
	('corp_cup', "CORP Cup: opening bans only, loser picks")
)

PICK_RULESETS = (
	("loserpicks", "High Seed 1st Pick/Loser Picks"),
	("alternate", "Alternate player picks"),
)

BAN_RULESETS = (
	("default", "No Defer/High Seed first"),
	("deferban", "High Seed can defer ban/picks first"),
	("deferboth", "High Seed can defer both ban/pick"),
	("bansave", "Ban Save alternating")
)

class CH_Name(pydantic.BaseModel):
	ch_name: str
	is_primary: bool

class PlayerConfig(pydantic.BaseModel):
	names_list: list[CH_Name] = []

#This is for v6 steg
class StegScreenshotPlayer(pydantic.BaseModel):
	accent_notes_hit : int = 0
	accent_notes_total : int = 0
	audio_calibration : float = 0
	base_score : int = 0
	controller_type : str
	difficulty : str = "Expert"
	frets_ghosted : int = 0
	gamepad_mode : bool = False
	ghost_notes_hit : int = 0
	ghost_notes_total : int = 0
	instrument : str = "Guitar"
	is_bot : bool = False
	is_fc : bool = False
	lefty_flip : bool = False
	max_streak : int = 0
	modifiers :  list = ['NoModifiers']
	no_fail : bool = False
	notes_hit : int = 0
	profile_name : str = "Some Player"
	remote_network_player : bool = False
	score : int = 0
	solo_bonus_total : int = 0
	sp_phrases_earned : int = 0
	sp_phrases_total : int = 0
	total_notes : int = 0
	versus_winner : bool = False
	video_calibration : float = 0
	excess_hits : int = 0
	notes_missed : int = 0

#For manual non-ingame submissions w/ v6 steg
class StegScreenshotManual6(pydantic.BaseModel):
	profile_name : str = "Manual Player Entry"
	error_reason : str = "Manual Player Non In-Game Screenshot Submission"

	score : int = 0
	is_fc : bool = False
	total_notes : int = 0
	notes_hit : int = 0
	notes_missed : int = 0
	max_streak : int = 0
	avg_multiplier : float = 0
	excess_hits : int = 0

	@property
	def sp_phrases_earned(self):
		return -1

	@property
	def sp_phrases_total(self):
		return -1

	@property
	def frets_ghosted(self):
		return -1

#For problem rounds
class StegScreenshotPlayerDummy(pydantic.BaseModel):
	profile_name : str = "Unknown Player"
	error_reason : str = "Unknown Error"
	__fields__ = ('profile_name', 'error_reason')

	#Generate dummy fields for other classes
	@property
	def score(self):
		return 0

	@property
	def total_notes(self):
		return 0

	@property
	def notes_missed(self):
		return 0

	@property
	def notes_hit(self):
		return 0

	@property
	def sp_phrases_earned(self):
		return 0

	@property
	def sp_phrases_total(self):
		return 0

	@property
	def frets_ghosted(self):
		return 0

	@property
	def is_fc(self):
		return False

	@property
	def excess_hits(self):
		return 0

#v10 6085-final
class StegSectionV10(pydantic.BaseModel):
	model_config = pydantic.ConfigDict(title=f'Steg v10 (6085)')

	section_name : str = "Some Section"
	notes_hit : int = 0
	notes_count : int = 0

	def __str__(self):
		return f"{self.section_name} : {self.notes_hit}/{self.notes_count}"

class StegScreenshotPlayerV10(StegScreenshotPlayer):
	avg_multiplier : float = 0
	clean_play_bonus : int = 0
	combo_score : int = 0
	note_score : int = 0
	solo_bonus_total : int = 0

	sp_activations : int = 0
	sp_bar_ticks : int = 0
	sp_score : int = 0
	sp_ticks_accumlated : int = 0

	squeeze_score : int = 0
	squeezed_notes : int = 0
	squeezed_notes_missed : int = 0

	sustain_score : int = 0
	time_in_sp : float = 0

	end_streak : int = 0
	failed_at : int = -1
	is_pfc : bool = False

	section_count : int = 0
	section_stats : list[StegSectionV10]

class StegScreenshot(pydantic.BaseModel):
	artist_name : str = "None"
	band_score : int = 0
	band_stars : int = 0
	song_name : str = "None"
	checksum : str = "None"
	charter_name : str = "None"
	game_version : str = CH_VERSIONS[0][0]
	game_mode : str = "Versus"
	playback_speed : int = 100
	players : list[ typing.Union[
		typing.Annotated[StegScreenshotPlayer, pydantic.Field(title='Steg v6 (4080)')] |
		typing.Annotated[StegScreenshotPlayerV10, pydantic.Field(title='Steg v10 (6085)')] |
		typing.Annotated[StegScreenshotPlayerDummy, pydantic.Field(title="Player Error/Issue")] |
		typing.Annotated[StegScreenshotManual6, pydantic.Field(title="Manual Player Screenshot Submission")]
	] ]
	score_timestamp: datetime = datetime.now()

#Encore API Models
class BaseEncoreModel(pydantic.BaseModel):
	model_config = pydantic.ConfigDict(
		alias_generator=pydantic.alias_generators.to_camel,
		populate_by_name=True,
		from_attributes=True
	)

class NoteCount(BaseEncoreModel):
	instrument: str
	difficulty: str
	count: int

class MaxNps(BaseEncoreModel):
	instrument: str
	difficulty: str
	nps: float
	time: typing.Optional[float] = None

class TrackHash(BaseEncoreModel):
	instrument: str
	difficulty: str
	hash: str

class ChartIssues(BaseEncoreModel):
	instrument: typing.Optional[str] = None
	difficulty: typing.Optional[str] = None
	note_issue: str
	description: str

class FolderIssues(BaseEncoreModel):
	folder_issue: str
	description: str

class MetadataIssues(BaseEncoreModel):
	metadata_issue: str
	description: str

class NotesData(BaseEncoreModel):
	instruments: typing.List[str]
	drum_type: typing.Optional[int] = None
	has_solo_sections: bool
	has_lyrics: bool
	has_vocals: bool
	has_forced_notes: bool
	has_tap_notes: bool
	has_open_notes: bool
	has_2x_kick: bool = pydantic.Field(alias="has2xKick")
	has_flex_lanes: bool
	chart_issues: typing.Optional[typing.List[ChartIssues]] = None
	note_counts: typing.List[NoteCount]
	max_nps: typing.List[MaxNps]
	track_hashes: typing.List[TrackHash]
	tempo_map_hash: str
	tempo_marker_count: int
	effective_length: float

class SongItem(BaseEncoreModel):
	ordering: int
	name: str
	artist: str
	album: str
	genre: str
	year: str
	chart_name: typing.Optional[str] = None
	chart_album: typing.Optional[str] = None
	chart_genre: typing.Optional[str] = None
	chart_year: typing.Optional[int] = None
	chart_id: int
	song_id: typing.Optional[int] = None
	group_id: int
	chart_drive_chart_id: int
	album_art_md5: typing.Optional[str] = None
	md5: str
	chart_hash: str
	version_group_id: int
	charter: str
	song_length: typing.Optional[int] = None

	diff_band: int
	diff_guitar: int
	diff_guitar_coop: int
	diff_rhythm: int
	diff_bass: int
	diff_drums: int
	diff_drums_real: int
	diff_keys: int
	diff_guitarghl: int
	diff_guitar_coop_ghl: int
	diff_rhythm_ghl: int
	diff_bassghl: int
	diff_vocals: int

	preview_start_time: int
	icon: str
	loading_phrase: str
	album_track: int
	playlist_track: int
	modchart: bool
	delay: int
	chart_offset: float
	hopo_frequency: int
	eighthnote_hopo: bool
	multiplier_note: int
	sustain_cutoff_threshold: int
	chord_snap_threshold: int
	video_start_time: typing.Optional[int] = None
	five_lane_drums: bool
	pro_drums: bool
	end_events: bool
	notes_data: NotesData
	folder_issues: typing.Optional[typing.List[FolderIssues]] = None
	metadata_issues: typing.Optional[typing.List[MetadataIssues]] = None
	has_video_background: bool
	modified_time: datetime
	application_drive_id: str
	application_username: typing.Optional[str] = None
	drums_reviewed: bool
	pack_name: typing.Optional[str] = None
	parent_folder_id: str
	drive_path: str
	drive_file_id: typing.Optional[str] = None
	drive_file_name: typing.Optional[str] = None
	drive_chart_is_pack: bool
	internal_path: str

class SearchResponse(BaseEncoreModel):
	found: int
	out_of: int
	page: int
	search_time_ms: int
	data: typing.List[SongItem]
